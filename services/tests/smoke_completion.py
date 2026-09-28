"""Optional live model smoke test inside the LLM container."""

import asyncio

import grpc

from services.proto import llm_pb2, llm_pb2_grpc


async def main():
    async with grpc.aio.insecure_channel("localhost:50052") as channel:
        result = await llm_pb2_grpc.LlmServiceStub(channel).Complete(
            llm_pb2.CompleteRequest(
                document_id="smoke-test",
                user_id="smoke-test",
                prefix="The quiet garden was filled with",
            ),
            timeout=60,
        )
    if not result.text.strip() or len(result.text) > 600:
        raise RuntimeError("The model returned an invalid completion.")
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
