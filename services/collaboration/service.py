"""Document authorization, durable Yjs updates, and in-process room fanout."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import asyncpg
import grpc
from pycrdt import Doc, XmlElement, XmlFragment

from services.proto import collaboration_pb2 as pb
from services.proto import collaboration_pb2_grpc as rpc

MAX_UPDATE_BYTES = 1_048_576
SNAPSHOT_INTERVAL = 100


@dataclass(eq=False)
class Subscriber:
    user_id: str
    closed: bool = False
    queue: asyncio.Queue[pb.ServerEvent | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=128)
    )


@dataclass
class Room:
    document: Doc
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: set[Subscriber] = field(default_factory=set)
    references: int = 0


def document_message(row: asyncpg.Record) -> pb.Document:
    return pb.Document(
        id=str(row["id"]),
        title=row["title"],
        owner_id=row["owner_clerk_id"],
        share_enabled=row["share_enabled"],
        updated_at=row["updated_at"].isoformat(),
    )


def document_uuid(raw_id: str) -> UUID:
    try:
        return UUID(raw_id)
    except ValueError as error:
        raise ValueError("Invalid document ID.") from error


class CollaborationServicer(rpc.CollaborationServiceServicer):
    """Owns document permissions and serializes writes within one service replica."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.rooms: dict[UUID, Room] = {}
        self.rooms_lock = asyncio.Lock()

    async def _authorized_row(self, document_id: UUID, user_id: str):
        row = await self.pool.fetchrow(
            "SELECT * FROM collaboration.documents WHERE id = $1", document_id
        )
        if row is None or (row["owner_clerk_id"] != user_id and not row["share_enabled"]):
            return None
        return row

    async def _room_for(self, document_id: UUID) -> Room:
        async with self.rooms_lock:
            room = self.rooms.get(document_id)
            if room is not None:
                room.references += 1
                return room

            document = Doc()
            async with self.pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT snapshot, snapshot_version FROM collaboration.documents WHERE id = $1",
                    document_id,
                )
                if row is None:
                    raise ValueError("Document does not exist.")
                if row["snapshot"] is not None:
                    document.apply_update(bytes(row["snapshot"]))
                updates = await connection.fetch(
                    "SELECT update FROM collaboration.document_updates "
                    "WHERE document_id = $1 AND sequence > $2 ORDER BY sequence",
                    document_id,
                    row["snapshot_version"],
                )
                for update in updates:
                    document.apply_update(bytes(update["update"]))

            room = Room(document=document, references=1)
            self.rooms[document_id] = room
            return room

    async def _release_room(self, document_id: UUID, room: Room):
        async with self.rooms_lock:
            room.references -= 1
            if room.references == 0 and not room.subscribers:
                self.rooms.pop(document_id, None)

    async def CreateDocument(self, request: pb.CreateDocumentRequest, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "User is required.")
        title = request.title.strip() or "Untitled document"
        if len(title) > 200:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Title is too long.")
        initial_document = Doc()
        initial_document["default"] = XmlFragment([XmlElement("paragraph")])
        row = await self.pool.fetchrow(
            "INSERT INTO collaboration.documents (id, owner_clerk_id, title, snapshot) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            uuid4(),
            request.user_id,
            title,
            initial_document.get_update(),
        )
        return document_message(row)

    async def ListDocuments(self, request: pb.ListDocumentsRequest, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "User is required.")
        rows = await self.pool.fetch(
            "SELECT * FROM collaboration.documents WHERE owner_clerk_id = $1 "
            "ORDER BY updated_at DESC",
            request.user_id,
        )
        return pb.ListDocumentsResponse(documents=[document_message(row) for row in rows])

    async def GetDocument(self, request: pb.GetDocumentRequest, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "User is required.")
        try:
            document_id = document_uuid(request.document_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid document ID.")
        row = await self._authorized_row(document_id, request.user_id)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        return document_message(row)

    async def SetSharing(self, request: pb.SetSharingRequest, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "User is required.")
        try:
            document_id = document_uuid(request.document_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid document ID.")

        async with self.rooms_lock:
            room = self.rooms.get(document_id)
            if room is not None:
                async with room.lock:
                    row = await self._set_sharing(
                        document_id, request.user_id, request.enabled, context
                    )
                    if not request.enabled:
                        for subscriber in tuple(room.subscribers):
                            if subscriber.user_id != request.user_id:
                                self._close(
                                    subscriber,
                                    pb.ServerEvent(access_revoked=pb.AccessRevoked()),
                                )
                                room.subscribers.discard(subscriber)
                    return document_message(row)

            row = await self._set_sharing(
                document_id, request.user_id, request.enabled, context
            )
            return document_message(row)

    async def _set_sharing(self, document_id: UUID, user_id: str, enabled: bool, context):
        row = await self.pool.fetchrow(
            "UPDATE collaboration.documents SET share_enabled = $1, updated_at = NOW() "
            "WHERE id = $2 AND owner_clerk_id = $3 RETURNING *",
            enabled,
            document_id,
            user_id,
        )
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        return row

    @staticmethod
    def _close(subscriber: Subscriber, last_event: pb.ServerEvent | None = None):
        if subscriber.closed:
            return
        subscriber.closed = True
        while not subscriber.queue.empty():
            subscriber.queue.get_nowait()
        if last_event is not None:
            subscriber.queue.put_nowait(last_event)
        subscriber.queue.put_nowait(None)

    def _send(self, subscriber: Subscriber, event: pb.ServerEvent) -> bool:
        if subscriber.closed:
            return False
        try:
            subscriber.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self._close(subscriber)
            return False

    def _broadcast(self, room: Room, event: pb.ServerEvent):
        for subscriber in tuple(room.subscribers):
            if not self._send(subscriber, event):
                room.subscribers.discard(subscriber)

    async def _apply_edit(
        self, room: Room, document_id: UUID, subscriber: Subscriber, edit: pb.Edit
    ):
        try:
            operation_id = UUID(edit.operation_id)
        except ValueError as error:
            raise ValueError("Invalid operation ID.") from error
        if not 0 < len(edit.update) <= MAX_UPDATE_BYTES:
            raise ValueError("Update must be between 1 byte and 1 MiB.")

        async with room.lock:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        "SELECT owner_clerk_id, share_enabled, version "
                        "FROM collaboration.documents WHERE id = $1 FOR UPDATE",
                        document_id,
                    )
                    if row is None or (
                        row["owner_clerk_id"] != subscriber.user_id
                        and not row["share_enabled"]
                    ):
                        self._close(
                            subscriber,
                            pb.ServerEvent(access_revoked=pb.AccessRevoked()),
                        )
                        room.subscribers.discard(subscriber)
                        return

                    previous = await connection.fetchrow(
                        "SELECT document_id FROM collaboration.document_updates "
                        "WHERE operation_id = $1",
                        operation_id,
                    )
                    if previous is not None:
                        if previous["document_id"] != document_id:
                            raise ValueError("Operation ID belongs to another document.")
                        self._send(
                            subscriber,
                            pb.ServerEvent(
                                acknowledgement=pb.EditAcknowledgement(
                                    operation_id=edit.operation_id
                                )
                            ),
                        )
                        return

                    next_document = Doc()
                    next_document.apply_update(room.document.get_update())
                    try:
                        next_document.apply_update(edit.update)
                    except (ValueError, RuntimeError) as error:
                        raise ValueError("Invalid document update.") from error

                    next_version = row["version"] + 1
                    await connection.execute(
                        "INSERT INTO collaboration.document_updates "
                        "(document_id, sequence, operation_id, actor_clerk_id, update) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        document_id,
                        next_version,
                        operation_id,
                        subscriber.user_id,
                        edit.update,
                    )
                    if next_version % SNAPSHOT_INTERVAL == 0:
                        await connection.execute(
                            "UPDATE collaboration.documents SET version = $1, "
                            "snapshot_version = $1, snapshot = $2, updated_at = NOW() "
                            "WHERE id = $3",
                            next_version,
                            next_document.get_update(),
                            document_id,
                        )
                    else:
                        await connection.execute(
                            "UPDATE collaboration.documents SET version = $1, "
                            "updated_at = NOW() WHERE id = $2",
                            next_version,
                            document_id,
                        )

            room.document = next_document
            self._send(
                subscriber,
                pb.ServerEvent(
                    acknowledgement=pb.EditAcknowledgement(operation_id=edit.operation_id)
                ),
            )
            self._broadcast(
                room,
                pb.ServerEvent(
                    edit=pb.CommittedEdit(
                        operation_id=edit.operation_id, update=edit.update
                    )
                ),
            )

    async def _rename(
        self, room: Room, document_id: UUID, subscriber: Subscriber, rename: pb.Rename
    ):
        title = rename.title.strip()
        if not 0 < len(title) <= 200:
            raise ValueError("Title must be between 1 and 200 characters.")
        try:
            operation_id = UUID(rename.operation_id)
        except ValueError as error:
            raise ValueError("Invalid operation ID.") from error

        async with room.lock:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        "SELECT owner_clerk_id, share_enabled, title "
                        "FROM collaboration.documents WHERE id = $1 FOR UPDATE",
                        document_id,
                    )
                    if row is None or (
                        row["owner_clerk_id"] != subscriber.user_id
                        and not row["share_enabled"]
                    ):
                        self._close(
                            subscriber,
                            pb.ServerEvent(access_revoked=pb.AccessRevoked()),
                        )
                        room.subscribers.discard(subscriber)
                        return
                    previous = await connection.fetchrow(
                        "SELECT document_id, title FROM collaboration.document_title_events "
                        "WHERE operation_id = $1",
                        operation_id,
                    )
                    if previous is not None:
                        if previous["document_id"] != document_id:
                            raise ValueError("Operation ID belongs to another document.")
                        self._send(
                            subscriber,
                            pb.ServerEvent(
                                title_changed=pb.TitleChanged(
                                    title=row["title"], operation_id=rename.operation_id
                                )
                            ),
                        )
                        return
                    await connection.execute(
                        "INSERT INTO collaboration.document_title_events "
                        "(operation_id, document_id, actor_clerk_id, title) "
                        "VALUES ($1, $2, $3, $4)",
                        operation_id,
                        document_id,
                        subscriber.user_id,
                        title,
                    )
                    await connection.execute(
                        "UPDATE collaboration.documents SET title = $1, updated_at = NOW() "
                        "WHERE id = $2",
                        title,
                        document_id,
                    )

            self._broadcast(
                room,
                pb.ServerEvent(
                    title_changed=pb.TitleChanged(
                        title=title, operation_id=rename.operation_id
                    )
                ),
            )

    async def Sync(self, request_iterator, context):
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Join is required.")
        if first.WhichOneof("event") != "join" or not first.join.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Join is required.")
        try:
            document_id = document_uuid(first.join.document_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid document ID.")

        row = await self._authorized_row(document_id, first.join.user_id)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")
        room = await self._room_for(document_id)
        subscriber = Subscriber(user_id=first.join.user_id)

        async with room.lock:
            row = await self._authorized_row(document_id, first.join.user_id)
            if row is not None:
                self._send(
                    subscriber,
                    pb.ServerEvent(
                        snapshot=pb.Snapshot(update=room.document.get_update(), title=row["title"])
                    ),
                )
                room.subscribers.add(subscriber)
        if row is None:
            await self._release_room(document_id, room)
            await context.abort(grpc.StatusCode.NOT_FOUND, "Document not found.")

        async def consume():
            try:
                async for event in request_iterator:
                    if subscriber.closed:
                        break
                    kind = event.WhichOneof("event")
                    if kind == "edit":
                        await self._apply_edit(room, document_id, subscriber, event.edit)
                    elif kind == "rename":
                        await self._rename(room, document_id, subscriber, event.rename)
                    else:
                        raise ValueError("Unexpected stream event.")
            except ValueError as error:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
            finally:
                self._close(subscriber)

        consumer = asyncio.create_task(consume())
        try:
            while True:
                event = await subscriber.queue.get()
                if event is None:
                    break
                yield event
            await consumer
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            room.subscribers.discard(subscriber)
            await self._release_room(document_id, room)
