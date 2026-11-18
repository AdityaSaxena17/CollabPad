"""Apply collaboration commands in committed log order on each replica."""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import grpc
from pycrdt import Doc, XmlElement, XmlFragment, merge_updates

from services.collaboration.service import MAX_UPDATE_BYTES, Subscriber
from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc as rpc
from services.raft.node import LeaderUnavailable
from services.raft.storage import Entry

# Consecutive edits by one person fold into a single version until they pause
# or the session runs long. Times come from the committed command, so every
# replica (and every replay) forms identical groups.
VERSION_IDLE_GAP = timedelta(minutes=2)
VERSION_MAX_SPAN = timedelta(minutes=10)


@dataclass
class Document:
    metadata: pb.Document
    body: Doc = field(default_factory=Doc)
    versions: list[tuple[pb.VersionMetadata, bytes | None]] = field(default_factory=list)
    checkpoints: dict[int, bytes] = field(default_factory=dict)
    # Start time of the newest version while it can still absorb edits.
    open_edit_started: datetime | None = None
    subscribers: set[Subscriber] = field(default_factory=set)


class ReplicatedCollaboration(rpc.CollaborationServiceServicer):
    def __init__(self):
        self.documents: dict[str, Document] = {}
        self.operations: dict[str, tuple[dict, grpc.StatusCode | None, int]] = {}
        self.node = None

    @staticmethod
    def _allowed(document, actor):
        return document is not None and (
            document.metadata.owner_id == actor or document.metadata.share_enabled
        )

    @staticmethod
    def _send(subscriber, event):
        if subscriber.closed:
            return
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            ReplicatedCollaboration._close(subscriber)

    @staticmethod
    def _close(subscriber, event=None):
        if subscriber.closed:
            return
        subscriber.closed = True
        while not subscriber.queue.empty():
            subscriber.queue.get_nowait()
        if event is not None:
            subscriber.queue.put_nowait(event)
        subscriber.queue.put_nowait(None)

    def apply(self, entry: Entry):
        command = json.loads(entry.command)
        key = command["operation_id"]
        if key in self.operations:
            return
        error = self._apply(command)
        self.operations[key] = command, error, entry.index

    def _apply(self, command):
        kind = command["kind"]
        actor = command["actor"]
        document_id = command["document_id"]
        document = self.documents.get(document_id)
        if kind in ("create", "import"):
            if document is not None:
                return grpc.StatusCode.ALREADY_EXISTS
            metadata = pb.Document(
                id=document_id, title=command["title"], owner_id=actor,
                updated_at=command.get("legacy_updated_at", command["time"]),
                share_enabled=command.get("enabled", False),
            )
            document = Document(metadata)
            document.body.apply_update(base64.b64decode(command["update"]))
            self.documents[document_id] = document
            self._record_version(document, command, base64.b64decode(command["update"]))
            return None
        if not self._allowed(document, actor):
            return grpc.StatusCode.NOT_FOUND
        if kind == "sharing":
            if document.metadata.owner_id != actor:
                return grpc.StatusCode.NOT_FOUND
            document.metadata.share_enabled = command["enabled"]
            if not command["enabled"]:
                for subscriber in tuple(document.subscribers):
                    if subscriber.user_id != actor:
                        self._close(subscriber, pb.ServerEvent(access_revoked=pb.AccessRevoked()))
                        document.subscribers.discard(subscriber)
            event = None
        elif kind == "rename":
            document.metadata.title = command["title"]
            event = pb.ServerEvent(title_changed=pb.TitleChanged(
                title=command["title"], operation_id=command["operation_id"],
            ))
        else:
            update = base64.b64decode(command["update"])
            try:
                document.body.apply_update(update)
            except (ValueError, RuntimeError):
                return grpc.StatusCode.INVALID_ARGUMENT
            event = pb.ServerEvent(edit=pb.CommittedEdit(
                update=update, operation_id=command["operation_id"],
            ))
        document.metadata.updated_at = command["time"]
        if kind in ("edit", "rename"):
            self._record_version(document, command, update if kind == "edit" else None)
        if event is not None and self.node.role == "leader":
            for subscriber in tuple(document.subscribers):
                self._send(subscriber, event)
        return None

    @staticmethod
    def _version_update(document, revision):
        checkpoint = max((index for index in document.checkpoints if index <= revision), default=0)
        updates = [document.checkpoints[checkpoint]] if checkpoint else []
        updates.extend(update for _, update in document.versions[checkpoint:revision] if update is not None)
        return merge_updates(*updates)

    def _record_version(self, document, command, update):
        if self._extend_edit_version(document, command, update):
            return
        document.open_edit_started = (
            datetime.fromisoformat(command["time"]) if command["kind"] == "edit" else None
        )
        revision = len(document.versions) + 1
        document.versions.append((pb.VersionMetadata(
            revision=revision, title=document.metadata.title, actor_id=command["actor"],
            timestamp=command.get("legacy_updated_at", command["time"]), kind=command["kind"],
        ), update))
        if revision % 100 == 0:
            document.checkpoints[revision] = self._version_update(document, revision)

    def _extend_edit_version(self, document, command, update):
        """Fold an edit into the newest version when it continues the same session."""
        if command["kind"] != "edit" or document.open_edit_started is None:
            return False
        last, last_update = document.versions[-1]
        now = datetime.fromisoformat(command["time"])
        if (
            last.actor_id != command["actor"]
            or now - datetime.fromisoformat(last.timestamp) > VERSION_IDLE_GAP
            or now - document.open_edit_started > VERSION_MAX_SPAN
        ):
            return False
        last.timestamp = command["time"]
        document.versions[-1] = (last, merge_updates(last_update, update))
        revision = len(document.versions)
        if revision in document.checkpoints:
            del document.checkpoints[revision]
            document.checkpoints[revision] = self._version_update(document, revision)
        return True

    async def _history_document(self, request, context):
        await self._require_user(request.user_id, context)
        document_id = await self._uuid(request.document_id, context)
        await self._read_barrier(context)
        document = self.documents.get(document_id)
        if document is None or document.metadata.owner_id != request.user_id:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        return document

    async def ListVersions(self, request, context):
        document = await self._history_document(request, context)
        end = min(request.before - 1, len(document.versions)) if request.before else len(document.versions)
        start = max(0, end - 25)
        return pb.ListVersionsResponse(
            versions=[meta for meta, _ in reversed(document.versions[start:end])],
            next_before=start + 1 if start else 0,
        )

    async def GetVersion(self, request, context):
        document = await self._history_document(request, context)
        if not 1 <= request.revision <= len(document.versions):
            await context.abort(grpc.StatusCode.NOT_FOUND, "Version not found.")
        return pb.DocumentVersion(
            metadata=document.versions[request.revision - 1][0],
            update=self._version_update(document, request.revision),
        )

    async def _read_barrier(self, context):
        try:
            await self.node.read_barrier()
        except LeaderUnavailable:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Raft leader unavailable.")

    async def _propose(self, context, kind, actor, document_id, operation_id, **payload):
        command = {
            "kind": kind, "actor": actor, "document_id": document_id,
            "operation_id": operation_id, "time": datetime.now(UTC).isoformat(), **payload,
        }
        try:
            index = await self.node.propose(json.dumps(command).encode())
        except LeaderUnavailable:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Raft leader unavailable.")
        previous, error, original_index = self.operations[operation_id]
        ignored = {"time", "update"} if kind == "create" else {"time"}
        if any(previous.get(key) != value for key, value in command.items() if key not in ignored):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Operation ID already used.")
        if error is not None:
            await context.abort(error, "Document operation rejected.")
        return original_index == index

    @staticmethod
    async def _uuid(value, context):
        try:
            return str(UUID(value))
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid identifier.")

    @staticmethod
    async def _require_user(user, context):
        if not user:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "User is required.")

    async def CreateDocument(self, request, context):
        await self._require_user(request.user_id, context)
        title = request.title.strip() or "Untitled document"
        if len(title) > 200:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Title is too long.")
        operation = await self._uuid(request.operation_id, context) if request.operation_id else str(uuid4())
        # Derive the document ID from the stable operation ID. The existing
        # committed command supplies the original CRDT bytes on a duplicate.
        document_id = str(uuid5(NAMESPACE_URL, f"collabpad:create:{operation}"))
        previous = self.operations.get(operation)
        if previous and previous[0]["kind"] == "create":
            update = previous[0]["update"]
        else:
            initial = Doc()
            initial["default"] = XmlFragment([XmlElement("paragraph")])
            update = base64.b64encode(initial.get_update()).decode()
        await self._propose(context, "create", request.user_id, document_id, operation,
                            title=title, update=update)
        return self.documents[document_id].metadata

    async def ImportDocument(self, request, context):
        legacy = request.document
        await self._require_user(legacy.owner_id, context)
        document_id = await self._uuid(legacy.id, context)
        operation = str(uuid5(NAMESPACE_URL, f"collabpad:import:{document_id}"))
        preview = Doc()
        try:
            preview.apply_update(request.update)
        except (ValueError, RuntimeError):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid legacy snapshot.")
        await self._propose(
            context, "import", legacy.owner_id, document_id, operation,
            title=legacy.title, enabled=legacy.share_enabled, legacy_updated_at=legacy.updated_at,
            update=base64.b64encode(request.update).decode(),
        )
        return self.documents[document_id].metadata

    async def ListDocuments(self, request, context):
        await self._require_user(request.user_id, context)
        await self._read_barrier(context)
        return pb.ListDocumentsResponse(documents=sorted(
            (doc.metadata for doc in self.documents.values() if doc.metadata.owner_id == request.user_id),
            key=lambda meta: (meta.updated_at, meta.id), reverse=True,
        ))

    async def GetDocument(self, request, context):
        await self._require_user(request.user_id, context)
        document_id = await self._uuid(request.document_id, context)
        await self._read_barrier(context)
        document = self.documents.get(document_id)
        if not self._allowed(document, request.user_id):
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        return document.metadata

    async def SetSharing(self, request, context):
        await self._require_user(request.user_id, context)
        document_id = await self._uuid(request.document_id, context)
        operation = await self._uuid(request.operation_id, context) if request.operation_id else str(uuid4())
        await self._propose(context, "sharing", request.user_id, document_id, operation,
                            enabled=request.enabled)
        return self.documents[document_id].metadata

    async def Sync(self, request_iterator, context):
        first = await anext(request_iterator, None)
        if first is None or first.WhichOneof("event") != "join":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Join is required.")
        actor = first.join.user_id
        await self._require_user(actor, context)
        document_id = await self._uuid(first.join.document_id, context)
        await self._read_barrier(context)
        document = self.documents.get(document_id)
        if not self._allowed(document, actor):
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        term = self.node.storage.state().term
        subscriber = Subscriber(user_id=actor)
        document.subscribers.add(subscriber)
        self._send(subscriber, pb.ServerEvent(snapshot=pb.Snapshot(
            title=document.metadata.title, update=document.body.get_update(),
            history_enabled=True, share_enabled=document.metadata.share_enabled,
        )))

        async def consume():
            async for incoming in request_iterator:
                kind = incoming.WhichOneof("event")
                if kind not in ("edit", "rename"):
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Unexpected stream event.")
                operation = await self._uuid(getattr(incoming, kind).operation_id, context)
                if not self._allowed(document, actor):
                    self._close(subscriber, pb.ServerEvent(access_revoked=pb.AccessRevoked()))
                    return
                if kind == "edit":
                    update = incoming.edit.update
                    if not 0 < len(update) <= MAX_UPDATE_BYTES:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid update size.")
                    await self._propose(context, kind, actor, document_id, operation,
                                        update=base64.b64encode(update).decode())
                    self._send(subscriber, pb.ServerEvent(
                        acknowledgement=pb.EditAcknowledgement(operation_id=operation),
                    ))
                else:
                    title = incoming.rename.title.strip()
                    if not 0 < len(title) <= 200:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid title.")
                    first_commit = await self._propose(context, kind, actor, document_id, operation, title=title)
                    if not first_commit:
                        self._send(subscriber, pb.ServerEvent(title_changed=pb.TitleChanged(
                            title=document.metadata.title, operation_id=operation,
                        )))

        async def watch_leader():
            async with self.node.changed:
                await self.node.changed.wait_for(
                    lambda: self.node.role != "leader" or self.node.storage.state().term != term
                )

        consumer = asyncio.create_task(consume())
        monitor = asyncio.create_task(watch_leader())
        receive = None
        try:
            while True:
                receive = asyncio.create_task(subscriber.queue.get())
                done, _ = await asyncio.wait(
                    (receive, consumer, monitor), return_when=asyncio.FIRST_COMPLETED,
                )
                if monitor in done:
                    await context.abort(grpc.StatusCode.UNAVAILABLE, "Raft leadership changed.")
                if receive in done:
                    event = receive.result()
                    if event is None:
                        return
                    yield event
                if consumer in done:
                    await consumer
                    if subscriber.queue.empty():
                        return
        finally:
            document.subscribers.discard(subscriber)
            tasks = [task for task in (consumer, monitor, receive) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
