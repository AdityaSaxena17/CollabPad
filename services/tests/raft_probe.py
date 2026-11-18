"""Write concurrent edits through a live Compose cluster and verify history."""

import asyncio
import os
import statistics
from uuid import uuid4

import grpc
from pycrdt import Doc, Text

from services.gateway.cluster import ClusterClient
from services.proto import collaboration_pb2 as pb


async def main():
    targets = [part.split("=", 1)[1] for part in os.environ["RAFT_PEERS"].split(",")]
    async with asyncio.timeout(180):
        client = ClusterClient(targets)
        try:
            await run(client)
        finally:
            await client.close()


async def run(client):
    owner = "raft-demo-probe"
    document = await client.CreateDocument(pb.CreateDocumentRequest(user_id=owner, title="Raft stress test"))
    print(f"Document {document.id}", flush=True)
    loop = asyncio.get_running_loop()
    latencies = []

    async def writer(number):
        local = Doc()
        local["probe"] = Text()
        stream = None
        try:
            for index in range(50):
                state = local.get_state()
                local["probe"] += f"[{number}:{index}]"
                operation = str(uuid4())
                event = pb.ClientEvent(edit=pb.Edit(operation_id=operation, update=local.get_update(state)))
                started = loop.time()
                while True:
                    try:
                        if stream is None:
                            stream = await client.open_sync()
                            await stream.write(pb.ClientEvent(join=pb.Join(document_id=document.id, user_id=owner)))
                            await stream.read()
                        await stream.write(event)
                        while True:
                            reply = await stream.read()
                            if reply is grpc.aio.EOF:
                                raise ConnectionError("Leader stream closed.")
                            if reply.acknowledgement.operation_id == operation:
                                break
                    except (grpc.aio.AioRpcError, asyncio.InvalidStateError, ConnectionError):
                        if stream is not None:
                            stream.cancel()
                            stream = None
                        await asyncio.sleep(0.12)
                    else:
                        break
                latencies.append(loop.time() - started)
                if len(latencies) == 20:
                    print("Twenty acknowledged edits", flush=True)
        finally:
            if stream is not None:
                stream.cancel()

    await asyncio.gather(*(writer(number) for number in range(10)))
    before = 0
    revisions = []
    while True:
        history = await client.ListVersions(pb.ListVersionsRequest(
            document_id=document.id, user_id=owner, before=before,
        ))
        revisions.extend(version.revision for version in history.versions)
        before = history.next_before
        if not before:
            break

    # All writers use the same owner, so rapid edits share one history session.
    # A pause during failover can split that session; revisions still must be
    # contiguous, with one create and at least one edit version.
    if not 2 <= len(revisions) <= 501 or revisions != list(range(len(revisions), 0, -1)):
        raise RuntimeError(f"Expected 2–501 contiguous history revisions, saw {revisions}.")
    version = await client.GetVersion(pb.GetVersionRequest(
        document_id=document.id, user_id=owner, revision=revisions[0],
    ))
    recovered = Doc()
    recovered.apply_update(version.update)
    recovered["probe"] = Text()
    content = str(recovered["probe"])
    for number in range(10):
        for index in range(50):
            if content.count(f"[{number}:{index}]") != 1:
                raise RuntimeError(f"Missing or duplicate edit {number}:{index}.")
    p95 = statistics.quantiles(latencies, n=20)[18]
    print(
        f"500 unique edits verified across {len(revisions)} history revisions. "
        f"Acknowledgement p95={p95:.3f}s, max={max(latencies):.3f}s.",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
