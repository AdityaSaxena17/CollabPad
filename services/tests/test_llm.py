"""Check completion and the remaining private gRPC task stubs."""

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

    async def test_completion_and_remaining_stubs(self):
        completed = await self.stub.Complete(
            pb.CompleteRequest(
                document_id=self.document_id, user_id="owner", prefix="Hello"
            ),
            timeout=5,
        )
        self.assertIn("suggestion", completed.text)
        calls = (
            self.stub.StartSummary(
                pb.SummaryRequest(
                    document_id=self.document_id, user_id="owner", text="Hello"
                ),
                timeout=5,
            ),
            self.stub.GetSummaryJob(
                pb.SummaryJobQuery(
                    document_id=self.document_id,
                    user_id="owner",
                    job_id=str(uuid4()),
                ),
                timeout=5,
            ),
            self.stub.Enhance(
                pb.EnhanceRequest(
                    document_id=self.document_id, user_id="owner", text="Hello"
                ),
                timeout=5,
            ),
        )
        for name, call in zip(("StartSummary", "GetSummaryJob", "Enhance"), calls):
            with self.subTest(method=name):
                with self.assertRaises(grpc.aio.AioRpcError) as raised:
                    await call
                self.assertEqual(raised.exception.code(), grpc.StatusCode.UNIMPLEMENTED)
