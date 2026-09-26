"""Exercise TTL-backed presence against the Compose Redis instance."""

import asyncio
import base64
import json
import unittest
from uuid import uuid4

from pycrdt import Decoder, Encoder

from services.gateway.presence import CHANNEL, EXPIRED_CHANNEL, LocalConnection, PresenceHub


class PresenceIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = PresenceHub(clerk=None)
        self.runner = asyncio.create_task(self.hub.run())
        for _ in range(100):
            if self.hub.available:
                break
            await asyncio.sleep(0.02)
        self.assertTrue(self.hub.available)
        self.connections = []

    async def asyncTearDown(self):
        for connection in self.connections:
            await self.hub.unregister(connection)
        self.runner.cancel()
        await asyncio.wait_for(asyncio.gather(self.runner, return_exceptions=True), 3)
        await self.hub.close()

    async def test_tabs_cursors_expiry_and_stale_index(self):
        document_id = str(uuid4())
        messages = []

        async def send(message):
            messages.append(message)

        async def wait_for_message(message_type, connection_id):
            for _ in range(150):
                for message in messages:
                    if message["type"] == message_type and (
                        message.get("connectionId") == connection_id
                        or message.get("connection", {}).get("connectionId") == connection_id
                    ):
                        return message
                await asyncio.sleep(0.02)
            self.fail(f"Expected {message_type} for {connection_id}")

        owner = LocalConnection(document_id, "owner", send, name="Real owner")
        guest = LocalConnection(document_id, "guest", send, name="Guest")
        guest_tab = LocalConnection(document_id, "guest", send, name="Guest")
        self.connections.extend((owner, guest, guest_tab))
        for connection in self.connections:
            await self.hub.register(connection)

        snapshot = await self.hub.snapshot(document_id)
        self.assertEqual(len(snapshot["connections"]), 3)
        self.assertEqual(
            {item["connectionId"] for item in snapshot["connections"]},
            {owner.connection_id, guest.connection_id, guest_tab.connection_id},
        )

        encoder = Encoder()
        encoder.write_var_uint(1)
        encoder.write_var_uint(1234)
        encoder.write_var_uint(1)
        encoder.write_var_string(json.dumps({"user": {"name": "Impersonator"}}))
        messages.clear()
        await self.hub.awareness_update(owner, encoder.to_bytes())
        changed = await wait_for_message("presence_upsert", owner.connection_id)
        self.assertIsNotNone(changed["connection"]["awareness"])
        self.assertFalse(any(message["type"] == "presence_snapshot" for message in messages))
        snapshot = await self.hub.snapshot(document_id)
        saved_owner = next(
            item for item in snapshot["connections"] if item["connectionId"] == owner.connection_id
        )
        decoder = Decoder(base64.b64decode(saved_owner["awareness"]))
        self.assertEqual(decoder.read_var_uint(), 1)
        self.assertEqual(decoder.read_var_uint(), 1234)
        decoder.read_var_uint()
        state = json.loads(decoder.read_var_string())
        self.assertEqual(state["user"]["name"], "Real owner")
        self.assertNotEqual(state["user"]["name"], "Impersonator")

        for connection, client_id in ((guest, 2345), (guest_tab, 3456)):
            tab_update = Encoder()
            tab_update.write_var_uint(1)
            tab_update.write_var_uint(client_id)
            tab_update.write_var_uint(1)
            tab_update.write_var_string("{}")
            await self.hub.awareness_update(connection, tab_update.to_bytes())
        snapshot = await self.hub.snapshot(document_id)
        self.assertEqual(
            {item["clientId"] for item in snapshot["connections"]},
            {1234, 2345, 3456},
        )
        self.assertEqual(
            await self.hub.redis.keys(f"presence:client:{document_id}:*"), []
        )
        await self.hub.refresh(owner)
        self.assertGreaterEqual(await self.hub.redis.ttl(owner.key), 44)

        changed_id = Encoder()
        changed_id.write_var_uint(1)
        changed_id.write_var_uint(4321)
        changed_id.write_var_uint(2)
        changed_id.write_var_string("{}")
        with self.assertRaisesRegex(ValueError, "client ID changed"):
            await self.hub.awareness_update(owner, changed_id.to_bytes())

        await self.hub.redis.sadd(owner.index_key, "stale-entry")
        snapshot = await self.hub.snapshot(document_id)
        self.assertEqual(len(snapshot["connections"]), 3)
        self.assertNotIn("stale-entry", await self.hub.redis.smembers(owner.index_key))

        # A delayed LEAVE or expiry must not remove a still-live connection.
        messages.clear()
        stale_notice = json.dumps({
            "documentId": document_id,
            "connectionId": owner.connection_id,
            "kind": "LEAVE",
        })
        await self.hub.redis.publish(CHANNEL, stale_notice)
        await self.hub.redis.publish(EXPIRED_CHANNEL, owner.key)
        await self.hub.redis.publish(CHANNEL, json.dumps({
            "documentId": document_id,
            "connectionId": guest_tab.connection_id,
            "kind": "AWARENESS",
        }))
        await wait_for_message("presence_upsert", guest_tab.connection_id)
        self.assertFalse(any(
            message["type"] == "presence_remove"
            and message["connectionId"] == owner.connection_id
            for message in messages
        ))

        # Simulate a vanished gateway: its socket cannot refresh or cleanly delete the key.
        guest.active = False
        self.hub.local[document_id].pop(guest.connection_id)
        messages.clear()
        await self.hub.redis.expire(guest.key, 1)
        await wait_for_message("presence_remove", guest.connection_id)
        snapshot = await self.hub.snapshot(document_id)
        self.assertEqual(len(snapshot["connections"]), 2)
        self.assertFalse(any(message["type"] == "presence_snapshot" for message in messages))
        await asyncio.sleep(2)
        self.assertTrue(self.hub.available)
        self.assertFalse(any(message["type"] == "presence_unavailable" for message in messages))

        # A broken Pub/Sub connection restores still-open sockets before a snapshot.
        await self.hub.redis.delete(owner.key, guest_tab.key, owner.index_key)
        messages.clear()
        await self.hub.subscriber.connection_pool.disconnect()
        for _ in range(150):
            if any(
                message["type"] == "presence_snapshot"
                and len(message["connections"]) == 2
                for message in messages
            ):
                break
            await asyncio.sleep(0.02)
        self.assertTrue(any(
            message["type"] == "presence_snapshot"
            and len(message["connections"]) == 2
            for message in messages
        ))
        self.assertEqual(len((await self.hub.snapshot(document_id))["connections"]), 2)
        messages.clear()
        await self.hub.unregister(guest_tab)
        await wait_for_message("presence_remove", guest_tab.connection_id)
        self.assertFalse(await self.hub.redis.exists(guest_tab.key))
        self.assertEqual(len((await self.hub.snapshot(document_id))["connections"]), 1)
