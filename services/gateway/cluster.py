"""Route browser-facing collaboration calls to the current Raft leader."""

import asyncio
from uuid import uuid4

import grpc

from services.proto import collaboration_pb2_grpc, raft_pb2, raft_pb2_grpc


class ClusterClient:
    def __init__(self, targets):
        self.channels = [
            grpc.aio.insecure_channel(target, options=[("grpc.max_receive_message_length", 4 * 1024 * 1024)])
            for target in targets
        ]
        self.stubs = [collaboration_pb2_grpc.CollaborationServiceStub(channel) for channel in self.channels]
        self.raft = [raft_pb2_grpc.RaftServiceStub(channel) for channel in self.channels]
        self.leader = None

    async def close(self):
        await asyncio.gather(*(channel.close() for channel in self.channels))

    async def discover(self):
        async def status(stub):
            try:
                return await stub.GetStatus(raft_pb2.StatusRequest(), timeout=0.3)
            except grpc.aio.AioRpcError:
                return None

        replies = await asyncio.gather(*(status(stub) for stub in self.raft))
        candidates = [
            (reply.term, index) for index, reply in enumerate(replies)
            if reply is not None and reply.role == "leader"
        ]
        self.leader = max(candidates)[1] if candidates else None
        return self.leader

    async def _call(self, method, request, timeout):
        if method in ("CreateDocument", "SetSharing") and not request.operation_id:
            request.operation_id = str(uuid4())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        error = None
        while loop.time() < deadline:
            index = self.leader if self.leader is not None else await self.discover()
            remaining = deadline - loop.time()
            if index is not None and remaining > 0:
                try:
                    return await getattr(self.stubs[index], method)(request, timeout=min(remaining, 3.1))
                except grpc.aio.AioRpcError as failure:
                    if failure.code() not in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                        raise
                    error = failure
                    if self.leader == index:
                        self.leader = None
            await asyncio.sleep(min(0.12, max(0, deadline - loop.time())))
        if error is not None:
            raise error
        raise grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), "No Raft leader available.")

    async def CreateDocument(self, request, timeout=5):
        return await self._call("CreateDocument", request, timeout)

    async def ListDocuments(self, request, timeout=5):
        return await self._call("ListDocuments", request, timeout)

    async def GetDocument(self, request, timeout=5):
        return await self._call("GetDocument", request, timeout)

    async def SetSharing(self, request, timeout=5):
        return await self._call("SetSharing", request, timeout)

    async def ImportDocument(self, request, timeout=5):
        return await self._call("ImportDocument", request, timeout)

    async def ListVersions(self, request, timeout=5):
        return await self._call("ListVersions", request, timeout)

    async def GetVersion(self, request, timeout=5):
        return await self._call("GetVersion", request, timeout)

    async def open_sync(self):
        index = await self.discover()
        if index is None:
            raise grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), "No Raft leader available.")
        return self.stubs[index].Sync()
