"""Start a collaboration replica and its private Raft peer service."""

import asyncio
import logging
import os
from pathlib import Path

import grpc

from services.collaboration.replicated import ReplicatedCollaboration
from services.proto import collaboration_pb2_grpc, raft_pb2_grpc
from services.raft.node import RaftNode
from services.raft.storage import RaftStorage


async def main():
    logging.basicConfig(level=logging.INFO)
    node_id = os.environ["RAFT_NODE_ID"]
    peers = dict(item.split("=", 1) for item in os.environ["RAFT_PEERS"].split(","))
    del peers[node_id]
    if len(peers) != 2:
        raise ValueError("Configure three collaboration replicas.")
    path = Path(os.environ.get("RAFT_DB", "/var/lib/collabpad/raft.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    storage = RaftStorage(path, node_id)
    collaboration = ReplicatedCollaboration()
    node = RaftNode(node_id, peers, storage, collaboration.apply)
    collaboration.node = node
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 4 * 1024 * 1024)])
    raft_pb2_grpc.add_RaftServiceServicer_to_server(node, server)
    collaboration_pb2_grpc.add_CollaborationServiceServicer_to_server(collaboration, server)
    server.add_insecure_port("[::]:50051")
    try:
        await node.start()
        await server.start()
        await server.wait_for_termination()
    finally:
        await server.stop(grace=1)
        await node.close()
        storage.close()


if __name__ == "__main__":
    asyncio.run(main())
