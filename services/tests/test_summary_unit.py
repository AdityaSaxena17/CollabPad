"""Summary chunking, job isolation, and shared-model scheduling."""

import asyncio
import unittest

import grpc

from services.llm.scheduler import ModelScheduler
from services.llm.service import LlmServicer
from services.llm.summary import (
    LlamaSummaryEngine,
    SUMMARY_JOB_TTL_SECONDS,
    clean_summary,
    split_for_context,
)
from services.proto import llm_pb2 as pb


class Aborted(Exception):
    def __init__(self, code):
        self.code = code


class FakeContext:
    async def abort(self, code, _details):
        raise Aborted(code)


class SummaryTextTest(unittest.IsolatedAsyncioTestCase):
    async def test_chunking_preserves_order_and_fits_token_budget(self):
        async def count(value):
            return len(value)

        source = "first paragraph\n\nsecond paragraph\n\n" + "verylongword" * 4
        chunks = await split_for_context(source, 18, count)
        self.assertTrue(all(len(chunk) <= 18 for chunk in chunks))
        self.assertIn("first paragraph", chunks)
        self.assertIn("second paragraph", chunks)
        self.assertEqual("".join(chunks).replace(" ", "").replace("\n", ""),
                         source.replace(" ", "").replace("\n", ""))

    async def test_model_summarizes_multiple_chunks_with_plain_text(self):
        class FakeModel:
            def __init__(self):
                self.prompts = []
                self.resets = 0

            def tokenize(self, value, add_bos=False):
                return list(value)

            def reset(self):
                self.resets += 1

            def create_completion(self, **kwargs):
                self.prompts.append(kwargs)
                return {"choices": [{"text": "Summary: <b>Short recap.</b>"}]}

        model = FakeModel()
        async def immediate(worker, *args):
            return worker(*args)

        scheduler = ModelScheduler(invoke=immediate)
        result = await LlamaSummaryEngine(model, 1024).summarize(
            "alpha beta gamma delta. " * 90, scheduler
        )
        self.assertEqual(result, "Short recap.")
        self.assertGreater(len(model.prompts), 2)
        self.assertEqual(model.resets, len(model.prompts))
        self.assertTrue(all(len(call["prompt"]) + call["max_tokens"] <= 1024
                            for call in model.prompts))
        self.assertEqual(model.prompts[-1]["max_tokens"], 256)
        await scheduler.close()

    async def test_summary_output_is_plain_text_and_bounded(self):
        self.assertEqual(clean_summary("Summary: <p>First</p><p>second &amp; third.</p>"),
                         "First second & third.")
        for bad in ("<b></b>", "x" * 2001, "bad\x00text", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    clean_summary(bad)


class SummaryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_requires_model_and_nonempty_input(self):
        service = LlmServicer()
        with self.assertRaises(Aborted) as raised:
            await service.StartSummary(pb.SummaryRequest(
                document_id="doc", user_id="owner", text="body"
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        with self.assertRaises(Aborted) as raised:
            await service.StartSummary(pb.SummaryRequest(
                document_id="doc", user_id="owner", text="   "
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.INVALID_ARGUMENT)
        await service.close()

    async def test_jobs_are_private_and_expire(self):
        release = asyncio.Event()

        class SummaryEngine:
            async def summarize(self, text, _scheduler):
                await release.wait()
                return f"<em>{text}</em>"

        service = LlmServicer(summary_engine=SummaryEngine())
        request = pb.SummaryRequest(document_id="doc", user_id="owner", text="The source")
        reference = await service.StartSummary(request, FakeContext())
        query = pb.SummaryJobQuery(document_id="doc", user_id="owner", job_id=reference.job_id)
        await asyncio.sleep(0)
        running = await service.GetSummaryJob(query, FakeContext())
        self.assertEqual(running.state, pb.SUMMARY_STATE_RUNNING)
        with self.assertRaises(Aborted) as raised:
            await service.GetSummaryJob(pb.SummaryJobQuery(
                document_id="doc", user_id="other", job_id=reference.job_id
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(Aborted) as raised:
            await service.StartSummary(request, FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        release.set()
        await service.summary_queue.join()
        completed = await service.GetSummaryJob(query, FakeContext())
        self.assertEqual(completed.state, pb.SUMMARY_STATE_COMPLETE)
        self.assertEqual(completed.text, "The source")
        service.summary_jobs[reference.job_id].finished_at -= SUMMARY_JOB_TTL_SECONDS + 1
        with self.assertRaises(Aborted) as raised:
            await service.GetSummaryJob(query, FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.NOT_FOUND)
        await service.close()

    async def test_queue_capacity_and_failure(self):
        release = asyncio.Event()

        class SummaryEngine:
            async def summarize(self, text, _scheduler):
                await release.wait()
                if text == "bad":
                    raise ValueError("private model detail")
                return "Valid summary."

        service = LlmServicer(summary_engine=SummaryEngine())
        for index in range(4):
            await service.StartSummary(pb.SummaryRequest(
                document_id=str(index), user_id="owner", text="bad" if index == 0 else "good"
            ), FakeContext())
        with self.assertRaises(Aborted) as raised:
            await service.StartSummary(pb.SummaryRequest(
                document_id="overflow", user_id="owner", text="good"
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        release.set()
        await service.summary_queue.join()
        failed = next(job for job in service.summary_jobs.values() if job.document_id == "0")
        self.assertEqual(failed.state, pb.SUMMARY_STATE_FAILED)
        self.assertNotIn("private model detail", failed.error)
        await service.close()


class ModelSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_calls_use_one_worker(self):
        scheduler = ModelScheduler()
        self.assertEqual(await asyncio.wait_for(scheduler.run(lambda: 1), 1), 1)
        self.assertEqual(await asyncio.wait_for(scheduler.run(lambda: 2), 1), 2)
        await scheduler.close()

    async def test_interactive_queue_timeout_does_not_interrupt_active_work(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed(worker, *args):
            entered.set()
            await release.wait()
            return worker(*args)

        scheduler = ModelScheduler(invoke=delayed)
        first = asyncio.create_task(scheduler.run(lambda: "first"))
        await asyncio.wait_for(entered.wait(), 1)
        with self.assertRaises(TimeoutError):
            await scheduler.run(lambda: "late", interactive=True, start_timeout=0.01)
        release.set()
        self.assertEqual(await first, "first")
        await scheduler.close()

    async def test_completion_runs_before_next_summary_step(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []

        def call(name):
            order.append(name)
            return name

        async def delayed(worker, name):
            if name == "first summary":
                entered.set()
                await release.wait()
            return worker(name)

        scheduler = ModelScheduler(invoke=delayed)
        first = asyncio.create_task(scheduler.run(call, "first summary"))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(scheduler.run(call, "second summary"))
        completion = asyncio.create_task(scheduler.run(call, "completion", interactive=True))
        await asyncio.sleep(0)
        release.set()
        self.assertEqual(await asyncio.gather(first, second, completion),
                         ["first summary", "second summary", "completion"])
        self.assertEqual(order, ["first summary", "completion", "second summary"])
        await scheduler.close()
