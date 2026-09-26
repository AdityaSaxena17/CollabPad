"""Start the single-replica collaboration gRPC service."""

import asyncio
import os

import asyncpg
import grpc

from services.collaboration.service import CollaborationServicer
from services.proto import collaboration_pb2_grpc


async def main():
    pool = await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=5432,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
        min_size=1,
        max_size=10,
        command_timeout=10,
    )
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 2_097_152)])
    collaboration_pb2_grpc.add_CollaborationServiceServicer_to_server(
        CollaborationServicer(pool), server
    )
    server.add_insecure_port("[::]:50051")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
