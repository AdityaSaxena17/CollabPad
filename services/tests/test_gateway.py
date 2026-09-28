"""Exercise the WebSocket-to-gRPC bridge with test identities."""

import asyncio
import base64
import os
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
from redis.exceptions import RedisError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pycrdt import Doc, Encoder, Text
from starlette.websockets import WebSocketDisconnect

from services.gateway.main import app


class GatewayIntegrationTest(unittest.TestCase):
    def test_two_socket_clients_sync_and_revocation_closes_guest(self):
        owner_id = f"test_owner_{uuid4()}"
        guest_id = f"test_guest_{uuid4()}"
        document_id = None

        def cleanup():
            if document_id is None:
                return

            async def remove_document():
                connection = await asyncpg.connect(
                    host=os.environ.get("POSTGRES_HOST", "postgres"),
                    user=os.environ["POSTGRES_USER"],
                    password=os.environ["POSTGRES_PASSWORD"],
                    database=os.environ["POSTGRES_DB"],
                )
                try:
                    await connection.execute(
                        "DELETE FROM collaboration.documents WHERE id = $1", UUID(document_id)
                    )
                finally:
                    await connection.close()

            asyncio.run(remove_document())

        self.addCleanup(cleanup)

        async def test_auth(token, _app):
            if token == "owner":
                return owner_id, int(time.time()) + 120
            if token == "short":
                return owner_id, int(time.time()) + 1
            if token == "guest":
                return guest_id, int(time.time()) + 120
            raise HTTPException(status_code=401, detail="Authentication required.")

        async def test_profile(_hub, connection):
            connection.name = "Test editor"

        def receive_until(socket, kind):
            for _ in range(10):
                message = socket.receive_json()
                if message["type"] == kind:
                    return message
            self.fail(f"Expected {kind} message")

        with patch.dict(os.environ, {"CLERK_SECRET_KEY": "integration-placeholder"}):
            with patch("services.gateway.main.verify_bearer", new=test_auth), patch(
                "services.gateway.presence.PresenceHub.resolve_profile", new=test_profile
            ):
                with TestClient(app) as client:
                    created = client.post(
                        "/api/documents",
                        headers={"Authorization": "Bearer owner"},
                        json={"title": "WebSocket integration"},
                    )
                    self.assertEqual(created.status_code, 201)
                    document_id = created.json()["id"]

                    denied = client.get(
                        f"/api/documents/{document_id}",
                        headers={"Authorization": "Bearer guest"},
                    )
                    self.assertEqual(denied.status_code, 404)
                    shared = client.patch(
                        f"/api/documents/{document_id}/sharing",
                        headers={"Authorization": "Bearer owner"},
                        json={"enabled": True},
                    )
                    self.assertEqual(shared.status_code, 200)

                    url = f"/ws/documents/{document_id}"
                    headers = {"origin": os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")}
                    with client.websocket_connect(url, headers=headers) as owner_socket:
                        with client.websocket_connect(url, headers=headers) as guest_socket:
                            owner_socket.send_json({"type": "auth", "token": "owner"})
                            guest_socket.send_json({"type": "auth", "token": "guest"})
                            for socket in (owner_socket, guest_socket):
                                self.assertEqual(socket.receive_json()["type"], "authenticated")
                                self.assertEqual(socket.receive_json()["type"], "snapshot")
                                self.assertEqual(
                                    receive_until(socket, "presence_ready")["type"],
                                    "presence_ready",
                                )

                            source = Doc()
                            source["text"] = Text("Live")
                            operation_id = str(uuid4())
                            owner_socket.send_json(
                                {
                                    "type": "edit",
                                    "id": operation_id,
                                    "update": base64.b64encode(source.get_update()).decode("ascii"),
                                }
                            )
                            self.assertEqual(receive_until(owner_socket, "ack"), {"type": "ack", "id": operation_id})
                            self.assertEqual(receive_until(owner_socket, "edit")["type"], "edit")
                            received = receive_until(guest_socket, "edit")
                            self.assertEqual(received["type"], "edit")
                            self.assertEqual(received["id"], operation_id)

                            presence_update = Encoder()
                            presence_update.write_var_uint(1)
                            presence_update.write_var_uint(17)
                            presence_update.write_var_uint(1)
                            presence_update.write_var_string("{}")
                            with patch.object(
                                app.state.presence,
                                "_write",
                                side_effect=RedisError("synthetic outage"),
                            ):
                                owner_socket.send_json({
                                    "type": "awareness",
                                    "update": base64.b64encode(presence_update.to_bytes()).decode("ascii"),
                                })
                                self.assertEqual(
                                    receive_until(owner_socket, "presence_unavailable")["type"],
                                    "presence_unavailable",
                                )
                                after_outage_id = str(uuid4())
                                owner_socket.send_json({
                                    "type": "edit",
                                    "id": after_outage_id,
                                    "update": base64.b64encode(source.get_update()).decode("ascii"),
                                })
                                self.assertEqual(
                                    receive_until(owner_socket, "ack")["id"], after_outage_id
                                )
                                self.assertEqual(
                                    receive_until(guest_socket, "edit")["id"], after_outage_id
                                )

                            revoked = client.patch(
                                f"/api/documents/{document_id}/sharing",
                                headers={"Authorization": "Bearer owner"},
                                json={"enabled": False},
                            )
                            self.assertEqual(revoked.status_code, 200)
                            self.assertEqual(receive_until(guest_socket, "access_revoked")["type"], "access_revoked")
                            with self.assertRaises(WebSocketDisconnect) as closed:
                                guest_socket.receive_json()
                            self.assertEqual(closed.exception.code, 1008)

                    with client.websocket_connect(url, headers=headers) as short_socket:
                        short_socket.send_json({"type": "auth", "token": "short"})
                        self.assertEqual(short_socket.receive_json()["type"], "authenticated")
                        self.assertEqual(short_socket.receive_json()["type"], "snapshot")
                        with self.assertRaises(WebSocketDisconnect) as expired:
                            while True:
                                short_socket.receive_json()
                        self.assertEqual(expired.exception.code, 1008)
