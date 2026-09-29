"""Enhancement prompt, shared queue, and private job lifecycle."""

import asyncio
import unittest

import grpc

from services.llm.enhancement import (
    LlamaEnhancementEngine,
    clean_enhancement,
)
from services.llm.scheduler import ModelScheduler
from services.llm.service import LlmServicer
from services.llm.summary import SUMMARY_JOB_TTL_SECONDS
from services.proto import llm_pb2 as pb


class Aborted(Exception):
    def __init__(self, code):
        self.code = code


class FakeContext:
    async def abort(self, code, _details):
        raise Aborted(code)


class EnhancementTextTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_resets_and_returns_only_plain_text(self):
        class FakeModel:
            def __init__(self):
                self.resets = 0
                self.calls = []

            def tokenize(self, value, add_bos=False):
                return list(value)

            def reset(self):
                self.resets += 1

            def create_completion(self, **kwargs):
                self.calls.append(kwargs)
                return {"choices": [{"text": "Rewrite: <em>Clear sentence.</em>"}]}

        async def immediate(worker, *args):
            return worker(*args)

        model = FakeModel()
        scheduler = ModelScheduler(invoke=immediate)
        engine = LlamaEnhancementEngine(model, 2048)
        self.assertEqual(await engine.improve("Unclear sentence.", scheduler), "Clear sentence.")
        self.assertEqual(await engine.improve("Another sentence.", scheduler), "Clear sentence.")
        self.assertEqual(model.resets, 2)
        self.assertTrue(all(call["max_tokens"] == 384 for call in model.calls))
        self.assertTrue(all("Source: " in call["prompt"] for call in model.calls))
        with self.assertRaises(ValueError):
            await LlamaEnhancementEngine(model, 400).improve("x" * 100, scheduler)
        await scheduler.close()

    async def test_output_is_one_paragraph_and_bounded(self):
        self.assertEqual(clean_enhancement("Improved: <b>Clear</b> &amp; short."), "Clear & short.")
        for bad in ("<b></b>", "First\nSecond", "x" * 4001, "bad\x00text", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    clean_enhancement(bad)


class EnhancementServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_validation_and_missing_model(self):
        service = LlmServicer()
        cases = (
            (pb.EnhanceRequest(document_id="doc", user_id="owner", text=" "), grpc.StatusCode.INVALID_ARGUMENT),
            (pb.EnhanceRequest(document_id="doc", user_id="owner", text="a" * 2001), grpc.StatusCode.RESOURCE_EXHAUSTED),
            (pb.EnhanceRequest(document_id="doc", user_id="owner", text="a\nb"), grpc.StatusCode.INVALID_ARGUMENT),
            (pb.EnhanceRequest(document_id="doc", user_id="owner", text="valid"), grpc.StatusCode.UNAVAILABLE),
        )
        for request, code in cases:
            with self.subTest(code=code), self.assertRaises(Aborted) as raised:
                await service.StartEnhancement(request, FakeContext())
            self.assertEqual(raised.exception.code, code)
        await service.close()

    async def test_jobs_are_private_bounded_and_expire(self):
        release = asyncio.Event()

        class SlowEnhancement:
            async def improve(self, text, _scheduler):
                await release.wait()
                return f"<em>{text}</em>"

        service = LlmServicer(enhancement_engine=SlowEnhancement())
        request = pb.EnhanceRequest(document_id="doc", user_id="owner", text="Source")
        reference = await service.StartEnhancement(request, FakeContext())
        query = pb.EnhancementJobQuery(document_id="doc", user_id="owner", job_id=reference.job_id)
        await asyncio.sleep(0)
        self.assertEqual((await service.GetEnhancementJob(query, FakeContext())).state,
                         pb.ENHANCEMENT_STATE_RUNNING)
        with self.assertRaises(Aborted) as raised:
            await service.GetEnhancementJob(pb.EnhancementJobQuery(
                document_id="doc", user_id="other", job_id=reference.job_id
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(Aborted) as raised:
            await service.StartEnhancement(request, FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        release.set()
        await service.enhancement_queue.join()
        self.assertEqual((await service.GetEnhancementJob(query, FakeContext())).text, "Source")
        self.assertEqual(service.enhancement_jobs[reference.job_id].source, "")
        service.enhancement_jobs[reference.job_id].finished_at -= SUMMARY_JOB_TTL_SECONDS + 1
        with self.assertRaises(Aborted) as raised:
            await service.GetEnhancementJob(query, FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.NOT_FOUND)
        await service.close()

    async def test_shared_limit_and_failed_job_do_not_expose_model_error(self):
        release = asyncio.Event()

        class SlowSummary:
            async def summarize(self, _text, _scheduler):
                await release.wait()
                return "A summary."

        class FailingEnhancement:
            async def improve(self, _text, _scheduler):
                raise ValueError("private model error")

        service = LlmServicer(summary_engine=SlowSummary(), enhancement_engine=FailingEnhancement())
        for index in range(3):
            await service.StartSummary(pb.SummaryRequest(
                document_id=f"summary-{index}", user_id="owner", text="source"
            ), FakeContext())
        reference = await service.StartEnhancement(pb.EnhanceRequest(
            document_id="enhancement", user_id="owner", text="source"
        ), FakeContext())
        with self.assertRaises(Aborted) as raised:
            await service.StartEnhancement(pb.EnhanceRequest(
                document_id="overflow", user_id="owner", text="source"
            ), FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        release.set()
        await asyncio.gather(service.summary_queue.join(), service.enhancement_queue.join())
        job = await service.GetEnhancementJob(pb.EnhancementJobQuery(
            document_id="enhancement", user_id="owner", job_id=reference.job_id
        ), FakeContext())
        self.assertEqual(job.state, pb.ENHANCEMENT_STATE_FAILED)
        self.assertNotIn("private model error", job.error)
        await service.close()
