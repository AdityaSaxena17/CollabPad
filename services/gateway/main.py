"""Browser-facing API and WebSocket bridge to the Python gRPC service."""

import asyncio
import base64
import binascii
import os
import time
from contextlib import asynccontextmanager

import grpc
import httpx
from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc as rpc

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
COLLABORATION_TARGET = os.environ.get("COLLABORATION_TARGET", "collaboration:50051")


@asynccontextmanager
async def lifespan(app: FastAPI):
    secret_key = os.environ.get("CLERK_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("CLERK_SECRET_KEY is required by the gateway.")
    with Clerk(bearer_auth=secret_key, timeout_ms=5_000) as clerk:
        channel = grpc.aio.insecure_channel(
            COLLABORATION_TARGET,
            options=[("grpc.max_receive_message_length", 2_097_152)],
        )
        app.state.clerk = clerk
        app.state.stub = rpc.CollaborationServiceStub(channel)
        try:
            yield
        finally:
            await channel.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


class CreateDocumentBody(BaseModel):
    title: str = "Untitled document"


class SetSharingBody(BaseModel):
    enabled: bool


def document_json(document: pb.Document) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "ownerId": document.owner_id,
        "shareEnabled": document.share_enabled,
        "updatedAt": document.updated_at,
    }


async def verify_bearer(token: str, app_instance: FastAPI) -> tuple[str, int]:
    if not token or len(token) > 8192:
        raise HTTPException(status_code=401, detail="Authentication required.")
    request = httpx.Request(
        "GET", FRONTEND_ORIGIN, headers={"Authorization": f"Bearer {token}"}
    )
    options = AuthenticateRequestOptions(
        authorized_parties=[FRONTEND_ORIGIN], accepts_token=["session_token"]
    )
    try:
        state = await asyncio.wait_for(
            asyncio.to_thread(app_instance.state.clerk.authenticate_request, request, options),
            timeout=6,
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail="Authentication unavailable.") from error
    payload = state.payload
    if not state.is_signed_in or payload is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = payload.get("sub")
    expires_at = payload.get("exp")
    if not isinstance(user_id, str) or not isinstance(expires_at, int):
        raise HTTPException(status_code=401, detail="Invalid session token.")
    return user_id, expires_at


async def current_user(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id, _ = await verify_bearer(authorization[7:], request.app)
    return user_id


def grpc_http_error(error: grpc.aio.AioRpcError) -> HTTPException:
    code = error.code()
    if code in (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.PERMISSION_DENIED):
        return HTTPException(status_code=404, detail="Document not found.")
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return HTTPException(status_code=400, detail=error.details())
    if code == grpc.StatusCode.UNAUTHENTICATED:
        return HTTPException(status_code=401, detail="Authentication required.")
    return HTTPException(status_code=503, detail="Collaboration service unavailable.")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/documents")
async def list_documents(request: Request):
    user_id = await current_user(request)
    try:
        response = await request.app.state.stub.ListDocuments(
            pb.ListDocumentsRequest(user_id=user_id), timeout=5
        )
    except grpc.aio.AioRpcError as error:
        raise grpc_http_error(error) from error
    return {"documents": [document_json(document) for document in response.documents]}


@app.post("/api/documents", status_code=201)
async def create_document(body: CreateDocumentBody, request: Request):
    user_id = await current_user(request)
    try:
        document = await request.app.state.stub.CreateDocument(
            pb.CreateDocumentRequest(user_id=user_id, title=body.title), timeout=5
        )
    except grpc.aio.AioRpcError as error:
        raise grpc_http_error(error) from error
    return document_json(document)


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str, request: Request):
    user_id = await current_user(request)
    try:
        document = await request.app.state.stub.GetDocument(
            pb.GetDocumentRequest(document_id=document_id, user_id=user_id), timeout=5
        )
    except grpc.aio.AioRpcError as error:
        raise grpc_http_error(error) from error
    return document_json(document)


@app.patch("/api/documents/{document_id}/sharing")
async def set_sharing(document_id: str, body: SetSharingBody, request: Request):
    user_id = await current_user(request)
    try:
        document = await request.app.state.stub.SetSharing(
            pb.SetSharingRequest(
                document_id=document_id, user_id=user_id, enabled=body.enabled
            ),
            timeout=5,
        )
    except grpc.aio.AioRpcError as error:
        raise grpc_http_error(error) from error
    return document_json(document)


@app.websocket("/ws/documents/{document_id}")
async def document_socket(websocket: WebSocket, document_id: str):
    if websocket.headers.get("origin") != FRONTEND_ORIGIN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if not isinstance(first, dict) or first.get("type") != "auth":
            await websocket.close(code=1008)
            return
        token = first.get("token")
        if not isinstance(token, str):
            await websocket.close(code=1008)
            return
        user_id, expires_at = await verify_bearer(token, websocket.app)
    except (TimeoutError, WebSocketDisconnect, ValueError, HTTPException):
        await websocket.close(code=1008)
        return

    call = websocket.app.state.stub.Sync()
    try:
        await call.write(pb.ClientEvent(join=pb.Join(document_id=document_id, user_id=user_id)))
    except grpc.aio.AioRpcError:
        await websocket.close(code=1011)
        return
    send_lock = asyncio.Lock()
    expiry = [expires_at]

    async def send(message: dict):
        async with send_lock:
            await websocket.send_json(message)

    await send({"type": "authenticated"})

    async def from_browser():
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            if not isinstance(message, dict):
                await websocket.close(code=1003)
                return
            kind = message.get("type")
            if kind == "auth":
                renewed_token = message.get("token")
                if not isinstance(renewed_token, str):
                    await websocket.close(code=1008)
                    return
                try:
                    renewed_id, renewed_expiry = await verify_bearer(
                        renewed_token, websocket.app
                    )
                except HTTPException:
                    await websocket.close(code=1008)
                    return
                if renewed_id != user_id:
                    await websocket.close(code=1008)
                    return
                expiry[0] = renewed_expiry
                await send({"type": "authenticated"})
            elif kind == "edit":
                operation_id = message.get("id")
                encoded = message.get("update")
                if not isinstance(operation_id, str) or not isinstance(encoded, str):
                    await websocket.close(code=1003)
                    return
                try:
                    update = base64.b64decode(encoded, validate=True)
                except binascii.Error:
                    await websocket.close(code=1003)
                    return
                if not 0 < len(update) <= 1_048_576:
                    await websocket.close(code=1009)
                    return
                await call.write(
                    pb.ClientEvent(edit=pb.Edit(operation_id=operation_id, update=update))
                )
            elif kind == "rename":
                operation_id = message.get("id")
                title = message.get("title")
                if not isinstance(operation_id, str) or not isinstance(title, str):
                    await websocket.close(code=1003)
                    return
                await call.write(
                    pb.ClientEvent(
                        rename=pb.Rename(operation_id=operation_id, title=title)
                    )
                )
            else:
                await websocket.close(code=1003)
                return

    async def from_collaboration():
        try:
            async for event in call:
                kind = event.WhichOneof("event")
                if kind == "snapshot":
                    await send(
                        {
                            "type": "snapshot",
                            "update": base64.b64encode(event.snapshot.update).decode("ascii"),
                            "title": event.snapshot.title,
                        }
                    )
                elif kind == "edit":
                    await send(
                        {
                            "type": "edit",
                            "id": event.edit.operation_id,
                            "update": base64.b64encode(event.edit.update).decode("ascii"),
                        }
                    )
                elif kind == "acknowledgement":
                    await send({"type": "ack", "id": event.acknowledgement.operation_id})
                elif kind == "title_changed":
                    await send(
                        {
                            "type": "title",
                            "id": event.title_changed.operation_id,
                            "title": event.title_changed.title,
                        }
                    )
                elif kind == "access_revoked":
                    await send({"type": "access_revoked"})
                    await websocket.close(code=1008)
                    return
            await send({"type": "service_unavailable"})
            await websocket.close(code=1011)
        except grpc.aio.AioRpcError as error:
            if error.code() in (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.PERMISSION_DENIED):
                await send({"type": "access_denied"})
                await websocket.close(code=1008)
            else:
                await send({"type": "service_unavailable"})
                await websocket.close(code=1011)

    async def watch_expiry():
        while True:
            await asyncio.sleep(1)
            if time.time() >= expiry[0]:
                await websocket.close(code=1008)
                return

    tasks = {
        asyncio.create_task(from_browser()),
        asyncio.create_task(from_collaboration()),
        asyncio.create_task(watch_expiry()),
    }
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        call.cancel()
