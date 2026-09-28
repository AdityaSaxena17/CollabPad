"""Optional live-model summary smoke test inside the LLM container."""

import asyncio
from uuid import uuid4

import grpc

from services.proto import llm_pb2 as pb
from services.proto import llm_pb2_grpc as rpc


async def main():
    async with grpc.aio.insecure_channel("localhost:50052") as channel:
        stub = rpc.LlmServiceStub(channel)
        document_id = str(uuid4())
        reference = await stub.StartSummary(pb.SummaryRequest(
            document_id=document_id,
            user_id="smoke-test",
            text=(
                "The team launched a shared writing tool in March. "
                "Editors can work on the same document and see each other's changes.\n\n"
                "The next milestone is a private document summary. "
                "The summary should capture the main points without editing the document."
            ),
        ), timeout=10)
        query = pb.SummaryJobQuery(
            document_id=document_id, user_id="smoke-test", job_id=reference.job_id
        )
        for _ in range(150):
            job = await stub.GetSummaryJob(query, timeout=10)
            if job.state in (pb.SUMMARY_STATE_COMPLETE, pb.SUMMARY_STATE_FAILED):
                break
            await asyncio.sleep(2)
    if job.state != pb.SUMMARY_STATE_COMPLETE or not job.text.strip() or len(job.text) > 2000:
        raise RuntimeError(f"The model returned an invalid summary: {job.error}")
    print(job.text)


if __name__ == "__main__":
    asyncio.run(main())
