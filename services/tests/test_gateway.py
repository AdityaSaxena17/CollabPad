"""Exercise the WebSocket-to-gRPC bridge with test identities."""

import asyncio
import base64
import os
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pycrdt import Doc, Text
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

        with patch.dict(os.environ, {"CLERK_SECRET_KEY": "integration-placeholder"}):
            with patch("services.gateway.main.verify_bearer", new=test_auth):
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
                    headers = {"origin": "http://localhost:3000"}
                    with client.websocket_connect(url, headers=headers) as owner_socket:
                        with client.websocket_connect(url, headers=headers) as guest_socket:
                            owner_socket.send_json({"type": "auth", "token": "owner"})
                            guest_socket.send_json({"type": "auth", "token": "guest"})
                            for socket in (owner_socket, guest_socket):
                                self.assertEqual(socket.receive_json()["type"], "authenticated")
                                self.assertEqual(socket.receive_json()["type"], "snapshot")

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
                            self.assertEqual(owner_socket.receive_json(), {"type": "ack", "id": operation_id})
                            self.assertEqual(owner_socket.receive_json()["type"], "edit")
                            received = guest_socket.receive_json()
                            self.assertEqual(received["type"], "edit")
                            self.assertEqual(received["id"], operation_id)

                            revoked = client.patch(
                                f"/api/documents/{document_id}/sharing",
                                headers={"Authorization": "Bearer owner"},
                                json={"enabled": False},
                            )
                            self.assertEqual(revoked.status_code, 200)
                            self.assertEqual(guest_socket.receive_json()["type"], "access_revoked")
                            with self.assertRaises(WebSocketDisconnect) as closed:
                                guest_socket.receive_json()
                            self.assertEqual(closed.exception.code, 1008)

                    with client.websocket_connect(url, headers=headers) as short_socket:
                        short_socket.send_json({"type": "auth", "token": "short"})
                        self.assertEqual(short_socket.receive_json()["type"], "authenticated")
                        self.assertEqual(short_socket.receive_json()["type"], "snapshot")
                        with self.assertRaises(WebSocketDisconnect) as expired:
                            short_socket.receive_json()
                        self.assertEqual(expired.exception.code, 1008)
