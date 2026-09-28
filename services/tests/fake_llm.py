"""Deterministic test-only gRPC server using the production task service."""

import asyncio

import grpc

from services.llm.service import LlmServicer
from services.proto import llm_pb2_grpc


class FakeCompletionEngine:
    def __init__(self):
        self.count = 0

    def generate(self, prefix: str, suffix: str) -> str:
        self.count += 1
        return f" suggestion {self.count}\nnext line"


class FakeSummaryEngine:
    async def summarize(self, text: str, _scheduler) -> str:
        return f"Summary of: {text[:80]}"


async def main():
    server = grpc.aio.server()
    llm_pb2_grpc.add_LlmServiceServicer_to_server(
        LlmServicer(FakeCompletionEngine(), FakeSummaryEngine()), server
    )
    server.add_insecure_port("[::]:50052")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)


if __name__ == "__main__":
    asyncio.run(main())
