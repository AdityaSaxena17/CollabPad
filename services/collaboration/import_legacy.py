"""Import a read-only PostgreSQL snapshot through the Raft leader."""

import asyncio
import os

import asyncpg
from pycrdt import Doc, merge_updates

from services.gateway.cluster import ClusterClient
from services.proto import collaboration_pb2 as pb


async def main():
    client = ClusterClient([part.split("=", 1)[1] for part in os.environ["RAFT_PEERS"].split(",")])
    try:
        connection = await asyncpg.connect(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            database=os.environ["POSTGRES_DB"],
            timeout=5,
        )
        try:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                async for row in connection.cursor(
                    "SELECT * FROM collaboration.documents ORDER BY id"
                ):
                    updates = await connection.fetch(
                        "SELECT update FROM collaboration.document_updates "
                        "WHERE document_id = $1 AND sequence > $2 ORDER BY sequence",
                        row["id"], row["snapshot_version"],
                    )
                    parts = [row["snapshot"]] if row["snapshot"] is not None else []
                    parts.extend(update["update"] for update in updates)
                    snapshot = merge_updates(*parts) if parts else Doc().get_update()
                    metadata = pb.Document(
                        id=str(row["id"]), owner_id=row["owner_clerk_id"], title=row["title"],
                        share_enabled=row["share_enabled"], updated_at=row["updated_at"].isoformat(),
                    )
                    await client.ImportDocument(
                        pb.ImportDocumentRequest(document=metadata, update=snapshot)
                    )
                    version = await client.GetVersion(pb.GetVersionRequest(
                        document_id=metadata.id, user_id=metadata.owner_id, revision=1,
                    ))
                    if version.metadata.kind != "import" or version.update != merge_updates(snapshot):
                        raise RuntimeError(f"Imported baseline verification failed for {metadata.id}")
                    print(f"Verified {metadata.id}")
        finally:
            await connection.close()
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
