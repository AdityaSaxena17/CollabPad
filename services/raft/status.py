"""Print the Raft status of each configured collaboration replica."""

import asyncio
import os

import grpc

from services.proto import raft_pb2, raft_pb2_grpc


async def main():
    targets = os.environ["RAFT_PEERS"].split(",")

    async def report(peer):
        _, target = peer.split("=", 1)
        async with grpc.aio.insecure_channel(target) as channel:
            stub = raft_pb2_grpc.RaftServiceStub(channel)
            try:
                state = await stub.GetStatus(raft_pb2.StatusRequest(), timeout=1)
            except grpc.aio.AioRpcError:
                print(f"{target} unavailable")
                return
            print(
                f"{state.node_id} {state.role} term={state.term} leader={state.leader_id or '-'} "
                f"commit={state.commit_index} applied={state.applied_index}"
            )

    await asyncio.gather(*(report(peer) for peer in targets))


if __name__ == "__main__":
    asyncio.run(main())
