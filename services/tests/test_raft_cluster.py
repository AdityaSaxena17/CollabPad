"""Exercise independent Raft nodes over their real gRPC transport."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import grpc
from pycrdt import Doc, XmlFragment, XmlText

from services.collaboration.replicated import ReplicatedCollaboration
from services.gateway.cluster import ClusterClient
from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc, raft_pb2_grpc
from services.raft.node import RaftNode
from services.raft.storage import RaftStorage


class RaftClusterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.replicas = {}
        self.servers = {}
        self.ports = {}
        for name in ("a", "b", "c"):
            self.prepare(name)
        self.targets = [f"127.0.0.1:{port}" for port in self.ports.values()]
        self.client = ClusterClient(self.targets)
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.stop_all)
        for name in self.ports:
            await self.start(name)

    def prepare(self, name):
        server = grpc.aio.server()
        port = server.add_insecure_port(f"127.0.0.1:{self.ports.get(name, 0)}")
        self.ports[name] = port
        self.servers[name] = server

    async def start(self, name):
        storage = RaftStorage(self.directory / f"{name}.sqlite3", name)
        app = ReplicatedCollaboration()
        peers = {key: f"127.0.0.1:{port}" for key, port in self.ports.items() if key != name}
        node = RaftNode(name, peers, storage, app.apply)
        app.node = node
        self.replicas[name] = node, app
        server = self.servers[name]
        raft_pb2_grpc.add_RaftServiceServicer_to_server(node, server)
        collaboration_pb2_grpc.add_CollaborationServiceServicer_to_server(app, server)
        await node.start()
        await server.start()

    async def stop(self, name):
        node, _ = self.replicas.pop(name)
        await self.servers[name].stop(0)
        await node.close()
        node.storage.close()

    async def stop_all(self):
        for name in list(self.replicas):
            await self.stop(name)

    async def leader(self):
        async with asyncio.timeout(6):
            while True:
                leaders = [name for name, (node, _) in self.replicas.items() if node.role == "leader"]
                if len(leaders) == 1:
                    return leaders[0]
                await asyncio.sleep(0.025)

    async def test_history_survives_leader_crash_and_follower_rejoin(self):
        leader = await self.leader()
        owner = "owner"
        document = await self.client.CreateDocument(pb.CreateDocumentRequest(user_id=owner))
        stream = await self.client.open_sync()
        self.addCleanup(stream.cancel)
        await stream.write(pb.ClientEvent(join=pb.Join(document_id=document.id, user_id=owner)))
        snapshot = await asyncio.wait_for(stream.read(), timeout=5)
        body = Doc()
        body.apply_update(snapshot.snapshot.update)
        body["default"] = XmlFragment()
        state = body.get_state()
        body["default"].children[0].children.append(XmlText("Saved before crash"))
        operation = str(uuid4())
        edit = pb.ClientEvent(edit=pb.Edit(operation_id=operation, update=body.get_update(state)))
        await stream.write(edit)
        for _ in range(2):
            event = await asyncio.wait_for(stream.read(), timeout=5)
            if event.HasField("acknowledgement"):
                self.assertEqual(event.acknowledgement.operation_id, operation)
                break
        else:
            self.fail("Missing committed acknowledgement")

        await self.stop(leader)
        self.assertNotEqual(await self.leader(), leader)
        history = await self.client.ListVersions(pb.ListVersionsRequest(document_id=document.id, user_id=owner))
        self.assertEqual([version.kind for version in history.versions], ["edit", "create"])
        version = await self.client.GetVersion(pb.GetVersionRequest(document_id=document.id, user_id=owner, revision=2))
        restored = Doc()
        restored.apply_update(version.update)
        restored["default"] = XmlFragment()
        self.assertIn("Saved before crash", str(restored["default"]))
        with self.assertRaises(grpc.aio.AioRpcError) as denied:
            await self.client.ListVersions(pb.ListVersionsRequest(document_id=document.id, user_id="guest"))
        self.assertEqual(denied.exception.code(), grpc.StatusCode.NOT_FOUND)

        retry = await self.client.open_sync()
        self.addCleanup(retry.cancel)
        await retry.write(pb.ClientEvent(join=pb.Join(document_id=document.id, user_id=owner)))
        self.assertTrue((await asyncio.wait_for(retry.read(), timeout=5)).HasField("snapshot"))
        await retry.write(edit)
        duplicate = await asyncio.wait_for(retry.read(), timeout=5)
        self.assertEqual(duplicate.acknowledgement.operation_id, operation)
        unchanged = await self.client.ListVersions(pb.ListVersionsRequest(
            document_id=document.id, user_id=owner,
        ))
        self.assertEqual(len(unchanged.versions), 2)

        self.prepare(leader)
        await self.start(leader)
        async with asyncio.timeout(6):
            while self.replicas[leader][0].applied < self.replicas[await self.leader()][0].applied:
                await asyncio.sleep(0.025)

        remaining = await self.leader()
        for name in list(self.replicas):
            if name != remaining:
                await self.stop(name)
        with self.assertRaises(grpc.aio.AioRpcError) as unavailable:
            await self.client.GetDocument(pb.GetDocumentRequest(document_id=document.id, user_id=owner), timeout=2)
        self.assertIn(unavailable.exception.code(), (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED))

    async def test_reads_do_not_append_log_entries(self):
        leader = await self.leader()
        owner = "owner"
        document = await self.client.CreateDocument(pb.CreateDocumentRequest(user_id=owner))
        node = self.replicas[leader][0]
        tip = node.storage.tip()[0]
        commit = node.storage.state().commit_index

        await self.client.GetDocument(pb.GetDocumentRequest(document_id=document.id, user_id=owner))
        await self.client.ListDocuments(pb.ListDocumentsRequest(user_id=owner))
        await self.client.ListVersions(pb.ListVersionsRequest(document_id=document.id, user_id=owner))
        stream = await self.client.open_sync()
        self.addCleanup(stream.cancel)
        await stream.write(pb.ClientEvent(join=pb.Join(document_id=document.id, user_id=owner)))
        self.assertTrue((await asyncio.wait_for(stream.read(), timeout=5)).HasField("snapshot"))

        self.assertEqual(node.storage.tip()[0], tip)
        self.assertEqual(node.storage.state().commit_index, commit)

    async def test_owner_deletes_document_and_open_editors_are_closed(self):
        await self.leader()
        owner, guest = "owner", "guest"
        document = await self.client.CreateDocument(pb.CreateDocumentRequest(user_id=owner))
        await self.client.SetSharing(pb.SetSharingRequest(document_id=document.id, user_id=owner, enabled=True))
        stream = await self.client.open_sync()
        self.addCleanup(stream.cancel)
        await stream.write(pb.ClientEvent(join=pb.Join(document_id=document.id, user_id=guest)))
        self.assertTrue((await asyncio.wait_for(stream.read(), timeout=5)).HasField("snapshot"))

        with self.assertRaises(grpc.aio.AioRpcError) as denied:
            await self.client.DeleteDocument(pb.DeleteDocumentRequest(document_id=document.id, user_id=guest))
        self.assertEqual(denied.exception.code(), grpc.StatusCode.NOT_FOUND)

        request = pb.DeleteDocumentRequest(document_id=document.id, user_id=owner)
        await self.client.DeleteDocument(request)
        self.assertTrue((await asyncio.wait_for(stream.read(), timeout=5)).HasField("document_deleted"))
        # A retry with the same operation ID (e.g. after a leader change) succeeds.
        await self.client.DeleteDocument(request)

        listed = await self.client.ListDocuments(pb.ListDocumentsRequest(user_id=owner))
        self.assertNotIn(document.id, [item.id for item in listed.documents])
        with self.assertRaises(grpc.aio.AioRpcError) as missing:
            await self.client.GetDocument(pb.GetDocumentRequest(document_id=document.id, user_id=owner))
        self.assertEqual(missing.exception.code(), grpc.StatusCode.NOT_FOUND)
        for name, (node, app) in self.replicas.items():
            async with asyncio.timeout(3):
                while document.id in app.documents:
                    await asyncio.sleep(0.025)
