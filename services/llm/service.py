"""Private gRPC service for completion and temporary AI jobs."""

import asyncio
import time
from dataclasses import dataclass
from uuid import uuid4

import grpc

from services.llm.completion import (
    CompletionEngine,
    MAX_INPUT_CHARS,
    clean_completion,
    is_insertable_completion,
    plain_text_completion,
)
from services.llm.enhancement import (
    EnhancementEngine,
    MAX_ENHANCEMENT_INPUT_CHARS,
    clean_enhancement,
)
from services.llm.scheduler import ModelScheduler
from services.llm.summary import (
    MAX_SUMMARY_INPUT_CHARS,
    SUMMARY_JOB_LIMIT,
    SUMMARY_JOB_TTL_SECONDS,
    SummaryEngine,
    clean_summary,
)
from services.proto import llm_pb2
from services.proto import llm_pb2_grpc


@dataclass
class _SummaryJob:
    document_id: str
    user_id: str
    source: str
    state: int = llm_pb2.SUMMARY_STATE_QUEUED
    text: str = ""
    error: str = ""
    finished_at: float | None = None


@dataclass
class _EnhancementJob:
    document_id: str
    user_id: str
    source: str
    state: int = llm_pb2.ENHANCEMENT_STATE_QUEUED
    text: str = ""
    error: str = ""
    finished_at: float | None = None


class LlmServicer(llm_pb2_grpc.LlmServiceServicer):
    def __init__(
        self,
        completion_engine: CompletionEngine | None = None,
        summary_engine: SummaryEngine | None = None,
        scheduler: ModelScheduler | None = None,
        enhancement_engine: EnhancementEngine | None = None,
    ):
        self.completion_engine = completion_engine
        self.summary_engine = summary_engine
        self.enhancement_engine = enhancement_engine
        self.scheduler = scheduler or ModelScheduler()
        self.summary_jobs: dict[str, _SummaryJob] = {}
        self.summary_queue: asyncio.Queue[str] = asyncio.Queue()
        self.summary_worker: asyncio.Task | None = None
        self.enhancement_jobs: dict[str, _EnhancementJob] = {}
        self.enhancement_queue: asyncio.Queue[str] = asyncio.Queue()
        self.enhancement_worker: asyncio.Task | None = None

    async def Complete(self, request, context):
        if len(request.prefix) + len(request.suffix) > MAX_INPUT_CHARS:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Input is too large.")
        if not request.prefix.strip() and not request.suffix.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Context is empty.")
        if self.completion_engine is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is not configured.")
        try:
            text = await self.scheduler.run(
                self.completion_engine.generate,
                request.prefix,
                request.suffix,
                interactive=True,
                start_timeout=10,
            )
            if isinstance(text, str):
                text = plain_text_completion(text)
                if not text.strip():
                    return llm_pb2.TextResult(text="")
            completion = clean_completion(text)
            if not is_insertable_completion(completion, request.prefix, request.suffix):
                completion = ""
            return llm_pb2.TextResult(text=completion)
        except TimeoutError:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is busy.")
        except Exception:
            await context.abort(grpc.StatusCode.INTERNAL, "Completion generation failed.")

    def _prune_jobs(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self.summary_jobs.items()):
            if job.finished_at is not None and now - job.finished_at >= SUMMARY_JOB_TTL_SECONDS:
                del self.summary_jobs[job_id]
        for job_id, job in list(self.enhancement_jobs.items()):
            if job.finished_at is not None and now - job.finished_at >= SUMMARY_JOB_TTL_SECONDS:
                del self.enhancement_jobs[job_id]

    def _pending_job_count(self) -> int:
        return sum(
            job.state in (llm_pb2.SUMMARY_STATE_QUEUED, llm_pb2.SUMMARY_STATE_RUNNING)
            for job in self.summary_jobs.values()
        ) + sum(
            job.state in (llm_pb2.ENHANCEMENT_STATE_QUEUED, llm_pb2.ENHANCEMENT_STATE_RUNNING)
            for job in self.enhancement_jobs.values()
        )

    async def StartSummary(self, request, context):
        if not request.document_id or not request.user_id or not request.text.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Summary context is empty.")
        if len(request.text) > MAX_SUMMARY_INPUT_CHARS:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Input is too large.")
        if self.summary_engine is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is not configured.")
        self._prune_jobs()
        pending = [
            job for job in self.summary_jobs.values()
            if job.state in (llm_pb2.SUMMARY_STATE_QUEUED, llm_pb2.SUMMARY_STATE_RUNNING)
        ]
        if self._pending_job_count() >= SUMMARY_JOB_LIMIT or any(
            job.document_id == request.document_id and job.user_id == request.user_id
            for job in pending
        ):
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Summary service is busy.")
        job_id = str(uuid4())
        self.summary_jobs[job_id] = _SummaryJob(
            document_id=request.document_id,
            user_id=request.user_id,
            source=request.text,
        )
        self.summary_queue.put_nowait(job_id)
        if self.summary_worker is None or self.summary_worker.done():
            self.summary_worker = asyncio.create_task(self._run_summaries())
        return llm_pb2.SummaryJobRef(job_id=job_id)

    async def _run_summaries(self) -> None:
        while not self.summary_queue.empty():
            job_id = self.summary_queue.get_nowait()
            job = self.summary_jobs[job_id]
            job.state = llm_pb2.SUMMARY_STATE_RUNNING
            try:
                result = await asyncio.wait_for(
                    self.summary_engine.summarize(job.source, self.scheduler),
                    timeout=30 * 60,
                )
                job.text = clean_summary(result)
                job.state = llm_pb2.SUMMARY_STATE_COMPLETE
            except TimeoutError:
                job.state = llm_pb2.SUMMARY_STATE_FAILED
                job.error = "Summary took too long. Try again."
            except Exception:
                job.state = llm_pb2.SUMMARY_STATE_FAILED
                job.error = "Could not summarize this document. Try again."
            finally:
                job.source = ""
                job.finished_at = time.monotonic()
                asyncio.get_running_loop().call_later(
                    SUMMARY_JOB_TTL_SECONDS + 1, self._prune_jobs
                )
                self.summary_queue.task_done()

    async def GetSummaryJob(self, request, context):
        self._prune_jobs()
        job = self.summary_jobs.get(request.job_id)
        if not job or job.document_id != request.document_id or job.user_id != request.user_id:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Summary job not found.")
        return llm_pb2.SummaryJobStatus(
            state=job.state, text=job.text, error=job.error
        )

    async def StartEnhancement(self, request, context):
        if not request.document_id or not request.user_id or not request.text.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Enhancement text is empty.")
        if len(request.text) > MAX_ENHANCEMENT_INPUT_CHARS:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Input is too large.")
        if "\n" in request.text or "\r" in request.text:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Selection must be one paragraph.")
        if self.enhancement_engine is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Local model is not configured.")
        self._prune_jobs()
        if self._pending_job_count() >= SUMMARY_JOB_LIMIT or any(
            job.document_id == request.document_id and job.user_id == request.user_id
            and job.state in (llm_pb2.ENHANCEMENT_STATE_QUEUED, llm_pb2.ENHANCEMENT_STATE_RUNNING)
            for job in self.enhancement_jobs.values()
        ):
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Enhancement service is busy.")
        job_id = str(uuid4())
        self.enhancement_jobs[job_id] = _EnhancementJob(
            document_id=request.document_id, user_id=request.user_id, source=request.text
        )
        self.enhancement_queue.put_nowait(job_id)
        if self.enhancement_worker is None or self.enhancement_worker.done():
            self.enhancement_worker = asyncio.create_task(self._run_enhancements())
        return llm_pb2.EnhancementJobRef(job_id=job_id)

    async def _run_enhancements(self) -> None:
        while not self.enhancement_queue.empty():
            job_id = self.enhancement_queue.get_nowait()
            job = self.enhancement_jobs[job_id]
            job.state = llm_pb2.ENHANCEMENT_STATE_RUNNING
            try:
                result = await asyncio.wait_for(
                    self.enhancement_engine.improve(job.source, self.scheduler),
                    timeout=30 * 60,
                )
                job.text = clean_enhancement(result)
                job.state = llm_pb2.ENHANCEMENT_STATE_COMPLETE
            except TimeoutError:
                job.state = llm_pb2.ENHANCEMENT_STATE_FAILED
                job.error = "Enhancement took too long. Try again."
            except Exception:
                job.state = llm_pb2.ENHANCEMENT_STATE_FAILED
                job.error = "Could not improve this selection. Try again."
            finally:
                job.source = ""
                job.finished_at = time.monotonic()
                asyncio.get_running_loop().call_later(
                    SUMMARY_JOB_TTL_SECONDS + 1, self._prune_jobs
                )
                self.enhancement_queue.task_done()

    async def GetEnhancementJob(self, request, context):
        self._prune_jobs()
        job = self.enhancement_jobs.get(request.job_id)
        if not job or job.document_id != request.document_id or job.user_id != request.user_id:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Enhancement job not found.")
        return llm_pb2.EnhancementJobStatus(
            state=job.state, text=job.text, error=job.error
        )

    async def close(self) -> None:
        if self.summary_worker is not None and not self.summary_worker.done():
            self.summary_worker.cancel()
            await asyncio.gather(self.summary_worker, return_exceptions=True)
        if self.enhancement_worker is not None and not self.enhancement_worker.done():
            self.enhancement_worker.cancel()
            await asyncio.gather(self.enhancement_worker, return_exceptions=True)
        await self.scheduler.close()
