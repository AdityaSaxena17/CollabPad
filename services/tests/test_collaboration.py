"""Exercise the running gRPC service against Docker PostgreSQL."""

import asyncio
import os
import unittest
from uuid import uuid4

import asyncpg
import grpc
from pycrdt import Doc, Text, XmlFragment, XmlText

from services.collaboration.service import CollaborationServicer, Room, Subscriber

from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc as rpc


class CollaborationIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            database=os.environ["POSTGRES_DB"],
            min_size=1,
            max_size=2,
        )
        self.channel = grpc.aio.insecure_channel("localhost:50051")
        self.stub = rpc.CollaborationServiceStub(self.channel)
        self.document_ids = []
        self.calls = []

    async def asyncTearDown(self):
        for call in self.calls:
            call.cancel()
        for document_id in self.document_ids:
            await self.pool.execute(
                "DELETE FROM collaboration.documents WHERE id = $1", document_id
            )
        await self.channel.close()
        await self.pool.close()

    async def join(self, document_id, user_id):
        call = self.stub.Sync()
        self.calls.append(call)
        await call.write(
            pb.ClientEvent(join=pb.Join(document_id=document_id, user_id=user_id))
        )
        snapshot = await asyncio.wait_for(call.read(), timeout=5)
        self.assertEqual(snapshot.WhichOneof("event"), "snapshot")
        return call, snapshot

    async def test_concurrent_edits_persist_and_sharing_revokes_guests(self):
        owner = f"test_owner_{uuid4()}"
        guest = f"test_guest_{uuid4()}"
        third = f"test_third_{uuid4()}"
        document = await self.stub.CreateDocument(
            pb.CreateDocumentRequest(user_id=owner, title="Integration test")
        )
        from uuid import UUID

        self.document_ids.append(UUID(document.id))
        self.assertFalse(document.share_enabled)

        with self.assertRaises(grpc.aio.AioRpcError) as denied:
            await self.stub.GetDocument(
                pb.GetDocumentRequest(document_id=document.id, user_id=guest)
            )
        self.assertEqual(denied.exception.code(), grpc.StatusCode.NOT_FOUND)

        owner_call, owner_snapshot = await self.join(document.id, owner)
        shared = await self.stub.SetSharing(
            pb.SetSharingRequest(document_id=document.id, user_id=owner, enabled=True)
        )
        self.assertTrue(shared.share_enabled)
        guest_call, guest_snapshot = await self.join(document.id, guest)
        third_call, third_snapshot = await self.join(document.id, third)

        left = Doc()
        left.apply_update(owner_snapshot.snapshot.update)
        left["default"] = XmlFragment()
        left_state = left.get_state()
        left["default"].children[0].children.append(XmlText("A"))
        right = Doc()
        right.apply_update(guest_snapshot.snapshot.update)
        right["default"] = XmlFragment()
        right_state = right.get_state()
        right["default"].children[0].children.append(XmlText("B"))
        third_document = Doc()
        third_document.apply_update(third_snapshot.snapshot.update)
        third_document["default"] = XmlFragment()
        third_state = third_document.get_state()
        third_document["default"].children[0].children.append(XmlText("C"))
        left_update = left.get_update(left_state)
        right_update = right.get_update(right_state)
        third_update = third_document.get_update(third_state)
        left_id = str(uuid4())
        right_id = str(uuid4())
        third_id = str(uuid4())
        await asyncio.gather(
            owner_call.write(
                pb.ClientEvent(edit=pb.Edit(operation_id=left_id, update=left_update))
            ),
            guest_call.write(
                pb.ClientEvent(edit=pb.Edit(operation_id=right_id, update=right_update))
            ),
            third_call.write(
                pb.ClientEvent(edit=pb.Edit(operation_id=third_id, update=third_update))
            ),
        )

        for call, own_id in (
            (owner_call, left_id),
            (guest_call, right_id),
            (third_call, third_id),
        ):
            events = [await asyncio.wait_for(call.read(), timeout=5) for _ in range(4)]
            self.assertEqual(
                {event.acknowledgement.operation_id for event in events if event.WhichOneof("event") == "acknowledgement"},
                {own_id},
            )
            self.assertEqual(
                {event.edit.operation_id for event in events if event.WhichOneof("event") == "edit"},
                {left_id, right_id, third_id},
            )

        await owner_call.write(
            pb.ClientEvent(edit=pb.Edit(operation_id=left_id, update=left_update))
        )
        duplicate_ack = await asyncio.wait_for(owner_call.read(), timeout=5)
        self.assertEqual(duplicate_ack.acknowledgement.operation_id, left_id)
        count = await self.pool.fetchval(
            "SELECT count(*) FROM collaboration.document_updates WHERE document_id = $1",
            self.document_ids[0],
        )
        self.assertEqual(count, 3)

        first_title_id = str(uuid4())
        second_title_id = str(uuid4())
        for call, operation_id, title in (
            (owner_call, first_title_id, "Owner title"),
            (guest_call, second_title_id, "Guest title"),
        ):
            await call.write(
                pb.ClientEvent(rename=pb.Rename(operation_id=operation_id, title=title))
            )
            for reader in (owner_call, guest_call, third_call):
                changed = await asyncio.wait_for(reader.read(), timeout=5)
                self.assertEqual(changed.WhichOneof("event"), "title_changed")
                self.assertEqual(changed.title_changed.title, title)

        await guest_call.write(
            pb.ClientEvent(
                rename=pb.Rename(operation_id=second_title_id, title="Guest title")
            )
        )
        duplicate_title = await asyncio.wait_for(guest_call.read(), timeout=5)
        self.assertEqual(duplicate_title.title_changed.title, "Guest title")

        new_call, snapshot = await self.join(document.id, owner)
        self.assertEqual(snapshot.snapshot.title, "Guest title")
        restored = Doc()
        restored.apply_update(snapshot.snapshot.update)
        restored["default"] = XmlFragment()
        paragraph = str(restored["default"])
        self.assertIn("A", paragraph)
        self.assertIn("B", paragraph)
        self.assertIn("C", paragraph)
        self.assertEqual(paragraph.count("<paragraph>"), 1)
        new_call.cancel()

        await self.stub.SetSharing(
            pb.SetSharingRequest(document_id=document.id, user_id=owner, enabled=False)
        )
        for call in (guest_call, third_call):
            revoked = await asyncio.wait_for(call.read(), timeout=5)
            self.assertEqual(revoked.WhichOneof("event"), "access_revoked")
        with self.assertRaises(grpc.aio.AioRpcError) as denied_again:
            await self.stub.GetDocument(
                pb.GetDocumentRequest(document_id=document.id, user_id=guest)
            )
        self.assertEqual(denied_again.exception.code(), grpc.StatusCode.NOT_FOUND)


class DatabaseFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_database_write_does_not_acknowledge_or_change_room(self):
        class FailingConnection:
            async def __aenter__(self):
                raise OSError("PostgreSQL unavailable")

            async def __aexit__(self, *_args):
                return False

        class FailingPool:
            def acquire(self):
                return FailingConnection()

        room = Room(document=Doc())
        subscriber = Subscriber(user_id="test_user")
        edit_source = Doc()
        edit_source["text"] = Text("Not committed")
        before = room.document.get_update()
        service = CollaborationServicer(FailingPool())

        with self.assertRaisesRegex(OSError, "PostgreSQL unavailable"):
            await service._apply_edit(
                room,
                uuid4(),
                subscriber,
                pb.Edit(operation_id=str(uuid4()), update=edit_source.get_update()),
            )

        self.assertTrue(subscriber.queue.empty())
        self.assertEqual(room.document.get_update(), before)
