"""Exercise document authorization and the LLM gRPC bridge."""

import asyncio
import os
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import grpc
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from services.gateway.main import CompletionBody, app, complete_text
from services.proto import llm_pb2


class AiGatewayTest(unittest.TestCase):
    def test_completion_passes_through_no_suggestion(self):
        async def authorized(_document_id, _request):
            return "doc-1", "user-1"

        class NoSuggestionLlm:
            async def Complete(self, *_args, **_kwargs):
                return llm_pb2.TextResult(text="")

        with patch("services.gateway.main.authorize_ai_document", new=authorized):
            with patch.object(app.state, "llm_stub", NoSuggestionLlm(), create=True):
                response = asyncio.run(
                    complete_text(
                        "doc-1",
                        CompletionBody(prefix="where are you", suffix=""),
                        Request({"type": "http", "app": app}),
                    )
                )
        self.assertEqual(response, {"text": ""})

    def test_ai_routes_validate_access_and_reach_the_stubs(self):
        owner_id = f"test_owner_{uuid4()}"
        guest_id = f"test_guest_{uuid4()}"
        document_id = None

        async def verify(token, _app):
            if token == "owner":
                return owner_id, int(time.time()) + 120
            if token == "guest":
                return guest_id, int(time.time()) + 120
            raise HTTPException(status_code=401, detail="Authentication required.")

        def cleanup():
            if document_id is None:
                return

            async def remove():
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

            asyncio.run(remove())

        self.addCleanup(cleanup)
        with patch.dict(os.environ, {"CLERK_SECRET_KEY": "integration-placeholder"}):
            with patch("services.gateway.main.verify_bearer", new=verify):
                with TestClient(app) as client:
                    created = client.post(
                        "/api/documents",
                        headers={"Authorization": "Bearer owner"},
                        json={"title": "AI test"},
                    )
                    self.assertEqual(created.status_code, 201)
                    document_id = created.json()["id"]
                    base = f"/api/documents/{document_id}/ai"
                    owner = {"Authorization": "Bearer owner"}
                    guest = {"Authorization": "Bearer guest"}

                    tasks = (
                        ("completion", {"prefix": "Continue this", "suffix": ""}),
                        ("summary", {"text": "A document to summarize."}),
                        ("enhancement", {"text": "Improve this sentence."}),
                    )
                    for task, body in tasks:
                        with self.subTest(task=task):
                            path = f"{base}/{task}"
                            denied = client.post(path, headers=guest, json=body)
                            self.assertEqual(denied.status_code, 404)
                            response = client.post(path, headers=owner, json=body)
                            if task == "completion":
                                self.assertEqual(response.status_code, 200)
                                self.assertIn("suggestion", response.json()["text"])
                            else:
                                self.assertEqual(response.status_code, 501)
                                self.assertEqual(
                                    response.json()["detail"],
                                    {"code": "task_not_implemented", "task": task},
                                )

                    no_session = client.post(
                        f"{base}/completion", json={"prefix": "Hello", "suffix": ""}
                    )
                    self.assertEqual(no_session.status_code, 401)
                    empty_completion = client.post(
                        f"{base}/completion", headers=owner,
                        json={"prefix": " ", "suffix": ""},
                    )
                    self.assertEqual(empty_completion.status_code, 400)
                    oversized_completion = client.post(
                        f"{base}/completion", headers=owner,
                        json={"prefix": "x" * 16_001, "suffix": ""},
                    )
                    self.assertEqual(oversized_completion.status_code, 413)

                    class NoSuggestionLlm:
                        async def Complete(self, *_args, **_kwargs):
                            return llm_pb2.TextResult(text="")

                    original_stub = app.state.llm_stub
                    app.state.llm_stub = NoSuggestionLlm()
                    try:
                        no_suggestion = client.post(
                            f"{base}/completion", headers=owner,
                            json={"prefix": "where are you", "suffix": ""},
                        )
                    finally:
                        app.state.llm_stub = original_stub
                    self.assertEqual(no_suggestion.status_code, 200)
                    self.assertEqual(no_suggestion.json(), {"text": ""})

                    blank = client.post(
                        f"{base}/enhancement", headers=owner, json={"text": "  "}
                    )
                    self.assertEqual(blank.status_code, 400)
                    oversized = client.post(
                        f"{base}/summary", headers=owner, json={"text": "a" * 100_001}
                    )
                    self.assertEqual(oversized.status_code, 413)
                    invalid_job = client.get(f"{base}/jobs/not-a-uuid", headers=owner)
                    self.assertEqual(invalid_job.status_code, 400)
                    pending_job = client.get(f"{base}/jobs/{uuid4()}", headers=owner)
                    self.assertEqual(pending_job.status_code, 501)

                    class UnavailableLlm:
                        async def Complete(self, *_args, **_kwargs):
                            raise grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE)

                    original_stub = app.state.llm_stub
                    app.state.llm_stub = UnavailableLlm()
                    try:
                        unavailable = client.post(
                            f"{base}/completion",
                            headers=owner,
                            json={"prefix": "Hello", "suffix": ""},
                        )
                    finally:
                        app.state.llm_stub = original_stub
                    self.assertEqual(unavailable.status_code, 503)

                    class SlowLlm:
                        async def Complete(self, *_args, **kwargs):
                            if kwargs["timeout"] != 60:
                                raise AssertionError("Completion deadline must be 60 seconds.")
                            raise grpc.aio.AioRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)

                    app.state.llm_stub = SlowLlm()
                    try:
                        timed_out = client.post(
                            f"{base}/completion", headers=owner,
                            json={"prefix": "Hello", "suffix": ""},
                        )
                    finally:
                        app.state.llm_stub = original_stub
                    self.assertEqual(timed_out.status_code, 504)
