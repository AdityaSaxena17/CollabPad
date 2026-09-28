"""Private gRPC task service; only completion has a model implementation."""

import asyncio
import grpc

from services.llm.completion import (
    CompletionEngine,
    MAX_INPUT_CHARS,
    clean_completion,
    is_insertable_completion,
    plain_text_completion,
)
from services.proto import llm_pb2
from services.proto import llm_pb2_grpc


class LlmServicer(llm_pb2_grpc.LlmServiceServicer):
    def __init__(self, completion_engine: CompletionEngine | None = None):
        self.completion_engine = completion_engine
        self.completion_lock = asyncio.Lock()

    async def Complete(self, request, context):
        if len(request.prefix) + len(request.suffix) > MAX_INPUT_CHARS:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Input is too large.")
        if not request.prefix.strip() and not request.suffix.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Context is empty.")
        if self.completion_engine is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is not configured.")
        try:
            await asyncio.wait_for(self.completion_lock.acquire(), timeout=0.05)
        except TimeoutError:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is busy.")
        release_on_finish = False
        try:
            generation = asyncio.create_task(
                asyncio.to_thread(
                    self.completion_engine.generate, request.prefix, request.suffix
                )
            )
            try:
                text = await asyncio.shield(generation)
            except asyncio.CancelledError:
                # A cancelled RPC must not free the model while its worker still runs.
                generation.add_done_callback(lambda _: self.completion_lock.release())
                release_on_finish = True
                raise
            if isinstance(text, str):
                text = plain_text_completion(text)
                if not text.strip():
                    return llm_pb2.TextResult(text="")
            completion = clean_completion(text)
            if not is_insertable_completion(completion, request.prefix, request.suffix):
                completion = ""
            return llm_pb2.TextResult(text=completion)
        except Exception:
            await context.abort(grpc.StatusCode.INTERNAL, "Completion generation failed.")
        finally:
            if not release_on_finish:
                self.completion_lock.release()

    async def StartSummary(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Summarization is not implemented.")

    async def GetSummaryJob(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Summarization is not implemented.")

    async def Enhance(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Content enhancement is not implemented.")
