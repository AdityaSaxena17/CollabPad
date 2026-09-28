"""Serialize access to one local model, favoring interactive completions."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any


@dataclass
class _Work:
    call: Callable[..., Any]
    args: tuple[Any, ...]
    started: asyncio.Future
    result: asyncio.Future


class ModelScheduler:
    def __init__(self, invoke: Callable[..., Awaitable[Any]] | None = None):
        self._interactive: deque[_Work] = deque()
        self._background: deque[_Work] = deque()
        self._worker: asyncio.Task | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-model")
        self._invoke = invoke
        self._closed = False

    async def run(
        self,
        call: Callable[..., Any],
        *args: Any,
        interactive: bool = False,
        start_timeout: float | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("The model scheduler is closed.")
        loop = asyncio.get_running_loop()
        started = loop.create_future()
        result = loop.create_future()
        queue = self._interactive if interactive else self._background
        queue.append(_Work(call, args, started, result))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())
        try:
            if start_timeout is not None:
                await asyncio.wait_for(started, timeout=start_timeout)
            else:
                await started
            return await result
        except TimeoutError:
            result.cancel()
            raise
        except asyncio.CancelledError:
            result.cancel()
            raise

    async def _drain(self) -> None:
        while True:
            if not self._interactive and not self._background:
                self._worker = None
                return
            queue = self._interactive if self._interactive else self._background
            work = queue.popleft()
            if work.result.cancelled():
                continue
            work.started.set_result(None)
            try:
                if self._invoke is not None:
                    value = await self._invoke(work.call, *work.args)
                else:
                    native_call = asyncio.get_running_loop().run_in_executor(
                        self._executor, partial(work.call, *work.args)
                    )
                    # A timer also wakes the loop if a native call's completion
                    # notification is delayed by the host runtime.
                    while not native_call.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(native_call), timeout=0.25)
                        except TimeoutError:
                            pass
                    value = native_call.result()
            except Exception as error:
                if not work.result.done():
                    work.result.set_exception(error)
            else:
                if not work.result.done():
                    work.result.set_result(value)

    async def close(self) -> None:
        self._closed = True
        for queue in (self._interactive, self._background):
            while queue:
                work = queue.popleft()
                work.started.cancel()
                work.result.cancel()
        if self._worker is not None and not self._worker.done():
            await self._worker
        self._executor.shutdown(wait=True, cancel_futures=True)
