"""Check completion, summaries, and the remaining private gRPC stub."""

import asyncio
import os
import unittest
from uuid import uuid4

import grpc

from services.proto import llm_pb2 as pb
from services.proto import llm_pb2_grpc as rpc


class LlmStubTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.channel = grpc.aio.insecure_channel(
            os.environ.get("LLM_TARGET", "llm:50052")
        )
        self.stub = rpc.LlmServiceStub(self.channel)
        self.document_id = str(uuid4())

    async def asyncTearDown(self):
        await self.channel.close()

    async def test_completion_summary_and_remaining_stub(self):
        completed = await self.stub.Complete(
            pb.CompleteRequest(
                document_id=self.document_id, user_id="owner", prefix="Hello"
            ),
            timeout=5,
        )
        self.assertIn("suggestion", completed.text)
        reference = await self.stub.StartSummary(
            pb.SummaryRequest(
                document_id=self.document_id, user_id="owner", text="Hello document"
            ), timeout=5,
        )
        query = pb.SummaryJobQuery(
            document_id=self.document_id, user_id="owner", job_id=reference.job_id
        )
        for _ in range(50):
            job = await self.stub.GetSummaryJob(query, timeout=5)
            if job.state == pb.SUMMARY_STATE_COMPLETE:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(job.state, pb.SUMMARY_STATE_COMPLETE)
        self.assertIn("Hello document", job.text)
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await self.stub.GetSummaryJob(pb.SummaryJobQuery(
                document_id=self.document_id, user_id="other", job_id=reference.job_id
            ), timeout=5)
        self.assertEqual(raised.exception.code(), grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await self.stub.Enhance(pb.EnhanceRequest(
                document_id=self.document_id, user_id="owner", text="Hello"
            ), timeout=5)
        self.assertEqual(raised.exception.code(), grpc.StatusCode.UNIMPLEMENTED)
