"""Optional real-model smoke check for selected-text rewrites."""

import asyncio
from uuid import uuid4

import grpc

from services.proto import llm_pb2 as pb
from services.proto import llm_pb2_grpc as rpc


async def main():
    async with grpc.aio.insecure_channel("localhost:50052") as channel:
        stub = rpc.LlmServiceStub(channel)
        document_id = str(uuid4())
        source = "The team had a meeting on Tuesday and they talked about the launch plan."
        reference = await stub.StartEnhancement(pb.EnhanceRequest(
            document_id=document_id, user_id="smoke-test", text=source
        ), timeout=10)
        query = pb.EnhancementJobQuery(
            document_id=document_id, user_id="smoke-test", job_id=reference.job_id
        )
        for _ in range(150):
            job = await stub.GetEnhancementJob(query, timeout=10)
            if job.state in (pb.ENHANCEMENT_STATE_COMPLETE, pb.ENHANCEMENT_STATE_FAILED):
                break
            await asyncio.sleep(2)
    if job.state != pb.ENHANCEMENT_STATE_COMPLETE or not job.text.strip() or len(job.text) > 4000:
        raise RuntimeError(f"The model returned an invalid rewrite: {job.error}")
    print(job.text)


if __name__ == "__main__":
    asyncio.run(main())
