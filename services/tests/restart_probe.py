"""Two-phase persistence probe: run `write`, restart collaboration, then `read ID`."""

import asyncio
import os
import sys
from uuid import UUID, uuid4

import asyncpg
import grpc
from pycrdt import Doc, XmlFragment, XmlText

from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc as rpc


async def main():
    """Writes a document or verifies and removes it after a service restart."""
    if len(sys.argv) < 2 or sys.argv[1] not in ("write", "read"):
        raise SystemExit("Usage: python -m services.tests.restart_probe write|read [document-id]")

    channel = grpc.aio.insecure_channel("localhost:50051")
    stub = rpc.CollaborationServiceStub(channel)
    owner = "test_restart_probe"
    try:
        if sys.argv[1] == "write":
            created = await stub.CreateDocument(
                pb.CreateDocumentRequest(user_id=owner, title="Restart probe")
            )
            print(created.id, flush=True)
            call = stub.Sync()
            await call.write(
                pb.ClientEvent(join=pb.Join(document_id=created.id, user_id=owner))
            )
            snapshot = await asyncio.wait_for(call.read(), timeout=5)
            document = Doc()
            document.apply_update(snapshot.snapshot.update)
            document["default"] = XmlFragment()
            initial_state = document.get_state()
            document["default"].children[0].children.append(XmlText("Survived restart"))
            operation_id = str(uuid4())
            await call.write(
                pb.ClientEvent(
                    edit=pb.Edit(
                        operation_id=operation_id,
                        update=document.get_update(initial_state),
                    )
                )
            )
            acknowledged = await asyncio.wait_for(call.read(), timeout=5)
            assert acknowledged.acknowledgement.operation_id == operation_id
            call.cancel()
        else:
            if len(sys.argv) != 3:
                raise SystemExit("The read phase requires a document ID.")
            document_id = UUID(sys.argv[2])
            call = stub.Sync()
            await call.write(
                pb.ClientEvent(join=pb.Join(document_id=str(document_id), user_id=owner))
            )
            snapshot = await asyncio.wait_for(call.read(), timeout=5)
            restored = Doc()
            restored.apply_update(snapshot.snapshot.update)
            restored["default"] = XmlFragment()
            assert str(restored["default"]) == "<paragraph>Survived restart</paragraph>"
            call.cancel()
            connection = await asyncpg.connect(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
                database=os.environ["POSTGRES_DB"],
            )
            try:
                await connection.execute(
                    "DELETE FROM collaboration.documents WHERE id = $1", document_id
                )
            finally:
                await connection.close()
            print("Restart persistence verified; probe document removed.")
    finally:
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())
