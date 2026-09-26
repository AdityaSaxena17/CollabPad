"""Ephemeral, authenticated document presence shared across gateway instances."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

import redis.asyncio as redis
from clerk_backend_api import Clerk
from pycrdt import Decoder, Encoder, StickyIndex
from redis.exceptions import RedisError
from starlette.websockets import WebSocketDisconnect

LOGGER = logging.getLogger(__name__)
CHANNEL = "presence:changes"
EXPIRED_CHANNEL = "__keyevent@0__:expired"
CONNECTION_PREFIX = "presence:connection:"
TTL_SECONDS = 45
INDEX_TTL_SECONDS = 90
COLORS = ("#0b57d0", "#b3261e", "#137333", "#8e24aa", "#ad5700", "#007b83")


@dataclass
class LocalConnection:
    document_id: str
    user_id: str
    send: Callable[[dict], Awaitable[None]]
    connection_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Editor"
    image_url: str | None = None
    color: str = COLORS[0]
    client_id: int | None = None
    clock: int = -1
    awareness: str | None = None
    last_awareness_at: float = 0
    active: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> str:
        return f"{CONNECTION_PREFIX}{self.document_id}:{self.connection_id}"

    @property
    def index_key(self) -> str:
        return f"presence:document:{self.document_id}"

    def value(self) -> str:
        return json.dumps(
            {
                "connectionId": self.connection_id,
                "userId": self.user_id,
                "name": self.name,
                "imageUrl": self.image_url,
                "color": self.color,
                "clientId": self.client_id,
                "awareness": self.awareness,
            },
            separators=(",", ":"),
        )


class PresenceHub:
    """Keeps Redis presence separate from document synchronization and saving."""

    def __init__(self, clerk: Clerk):
        self.clerk = clerk
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        self.redis = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        # Pub/Sub waits indefinitely for a message; command sockets retain short failure bounds.
        self.subscriber = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
        )
        self.local: dict[str, dict[str, LocalConnection]] = {}
        self.available = False
        self.fault = asyncio.Event()

    async def close(self):
        await self.redis.aclose()
        await self.subscriber.aclose()

    async def _subscriber_reconnected(self, _connection):
        # redis-py can transparently resubscribe; restart the recovery sequence too.
        self.fault.set()

    async def resolve_profile(self, connection: LocalConnection):
        """Use verified Clerk identity; profile lookup failures cannot block editing."""
        connection.color = COLORS[
            int.from_bytes(hashlib.sha256(connection.user_id.encode()).digest()[:2], "big")
            % len(COLORS)
        ]
        try:
            user = await asyncio.wait_for(
                asyncio.to_thread(self.clerk.users.get, user_id=connection.user_id),
                timeout=6,
            )
        except Exception:
            LOGGER.warning("Clerk profile lookup failed; using generic presence label")
            return
        full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
        connection.name = (full_name or user.username or "Editor")[:80]
        image_url = user.image_url
        if image_url and urlparse(image_url).scheme == "https":
            connection.image_url = image_url

    async def _mark_unavailable(self):
        if not self.available:
            return
        self.available = False
        for viewers in tuple(self.local.values()):
            for connection in tuple(viewers.values()):
                try:
                    await connection.send({"type": "presence_unavailable"})
                except (RuntimeError, WebSocketDisconnect):
                    # Its document socket is already closing; cleanup runs in that handler.
                    pass

    async def _write(self, connection: LocalConnection, kind: str):
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.set(connection.key, connection.value(), ex=TTL_SECONDS)
            pipeline.sadd(connection.index_key, connection.connection_id)
            pipeline.expire(connection.index_key, INDEX_TTL_SECONDS)
            pipeline.publish(
                CHANNEL,
                json.dumps(
                    {
                        "documentId": connection.document_id,
                        "connectionId": connection.connection_id,
                        "kind": kind,
                    }
                ),
            )
            await pipeline.execute()

    async def register(self, connection: LocalConnection):
        self.local.setdefault(connection.document_id, {})[connection.connection_id] = connection
        if not self.available:
            await connection.send({"type": "presence_unavailable"})
            return
        async with connection.lock:
            if not connection.active:
                return
            try:
                await self._write(connection, "JOIN")
                # The new viewer needs a complete roster; existing viewers get the JOIN delta.
                await connection.send(await self.snapshot(connection.document_id))
            except RedisError:
                await self._mark_unavailable()
                self.fault.set()

    async def unregister(self, connection: LocalConnection):
        connection.active = False
        async with connection.lock:
            viewers = self.local.get(connection.document_id)
            if viewers is not None:
                viewers.pop(connection.connection_id, None)
                if not viewers:
                    self.local.pop(connection.document_id, None)
            try:
                async with self.redis.pipeline(transaction=True) as pipeline:
                    pipeline.delete(connection.key)
                    pipeline.srem(connection.index_key, connection.connection_id)
                    pipeline.publish(
                        CHANNEL,
                        json.dumps(
                            {
                                "documentId": connection.document_id,
                                "connectionId": connection.connection_id,
                                "kind": "LEAVE",
                            }
                        ),
                    )
                    await pipeline.execute()
            except RedisError:
                await self._mark_unavailable()
                self.fault.set()

    async def refresh(self, connection: LocalConnection):
        if not self.available or not connection.active:
            return
        async with connection.lock:
            if not connection.active:
                return
            try:
                if await self.redis.expire(connection.key, TTL_SECONDS):
                    await self.redis.expire(connection.index_key, INDEX_TTL_SECONDS)
                else:
                    await self._write(connection, "JOIN")
            except RedisError:
                await self._mark_unavailable()
                self.fault.set()

    async def awareness_update(self, connection: LocalConnection, update: bytes):
        """Bind one Yjs awareness client ID to a socket and replace client-supplied identity."""
        if len(update) > 4096:
            raise ValueError("Awareness update is too large.")
        try:
            decoder = Decoder(update)
            if decoder.read_var_uint() != 1:
                raise ValueError("One awareness state is required.")
            client_id = decoder.read_var_uint()
            clock = decoder.read_var_uint()
            raw_state = decoder.read_var_string()
            if decoder.length != 0 or client_id > 0xFFFFFFFF or clock > 0xFFFFFFFF:
                raise ValueError("Invalid awareness update.")
            state = json.loads(raw_state)
            if state is not None and not isinstance(state, dict):
                raise ValueError("Invalid awareness state.")
            cursor = state.get("cursor") if state else None
            if cursor is not None and (
                not isinstance(cursor, dict)
                or not isinstance(cursor.get("anchor"), dict)
                or not isinstance(cursor.get("head"), dict)
            ):
                raise ValueError("Invalid awareness cursor.")
            if cursor is not None:
                StickyIndex.from_json(cursor["anchor"])
                StickyIndex.from_json(cursor["head"])
        except (IndexError, RuntimeError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid awareness update.") from error

        async with connection.lock:
            if not connection.active:
                return
            if connection.client_id is not None and connection.client_id != client_id:
                raise ValueError("Awareness client ID changed.")
            if clock <= connection.clock:
                return
            if connection.awareness is not None and time.monotonic() - connection.last_awareness_at < 0.1:
                return
            connection.client_id = client_id
            connection.clock = clock
            connection.last_awareness_at = time.monotonic()
            clean_state = {
                "user": {"name": connection.name, "color": connection.color},
                "cursor": cursor,
            }
            encoder = Encoder()
            encoder.write_var_uint(1)
            encoder.write_var_uint(client_id)
            encoder.write_var_uint(clock)
            encoder.write_var_string(json.dumps(clean_state, separators=(",", ":")))
            connection.awareness = base64.b64encode(encoder.to_bytes()).decode("ascii")
            if not self.available:
                return
            try:
                await self._write(connection, "AWARENESS")
            except RedisError:
                await self._mark_unavailable()
                self.fault.set()

    async def snapshot(self, document_id: str) -> dict:
        index_key = f"presence:document:{document_id}"
        ids = list(await self.redis.smembers(index_key))
        if not ids:
            return {"type": "presence_snapshot", "connections": []}
        values = await self.redis.mget(
            [f"{CONNECTION_PREFIX}{document_id}:{connection_id}" for connection_id in ids]
        )
        stale = [connection_id for connection_id, value in zip(ids, values) if value is None]
        if stale:
            await self.redis.srem(index_key, *stale)
        connections = [json.loads(value) for value in values if value is not None]
        return {"type": "presence_snapshot", "connections": connections}

    async def broadcast(self, document_id: str, event: dict):
        viewers = self.local.get(document_id)
        if not viewers or not self.available:
            return
        for connection in tuple(viewers.values()):
            async with connection.lock:
                if connection.active:
                    try:
                        await connection.send(event)
                    except (RuntimeError, WebSocketDisconnect):
                        # Its document socket is already closing; cleanup runs there.
                        pass

    async def _listen(self, subscription):
        async for message in subscription.listen():
            if message["type"] != "message":
                continue
            if message["channel"] == CHANNEL:
                try:
                    change = json.loads(message["data"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not isinstance(change, dict):
                    continue
                document_id = change.get("documentId")
                connection_id = change.get("connectionId")
                kind = change.get("kind")
                if (
                    not isinstance(document_id, str)
                    or not isinstance(connection_id, str)
                    or kind not in ("JOIN", "LEAVE", "AWARENESS")
                ):
                    continue
            else:
                key = message["data"]
                if not isinstance(key, str) or not key.startswith(CONNECTION_PREFIX):
                    continue
                document_id, separator, connection_id = key[len(CONNECTION_PREFIX) :].rpartition(":")
                if not separator:
                    continue
                kind = "LEAVE"
            if document_id not in self.local:
                continue
            value = await self.redis.get(f"{CONNECTION_PREFIX}{document_id}:{connection_id}")
            if value is None:
                await self.broadcast(
                    document_id,
                    {"type": "presence_remove", "connectionId": connection_id},
                )
            elif kind != "LEAVE":
                try:
                    connection = json.loads(value)
                except ValueError:
                    LOGGER.warning("Ignoring invalid Redis presence value")
                    continue
                if not isinstance(connection, dict) or connection.get("connectionId") != connection_id:
                    LOGGER.warning("Ignoring mismatched Redis presence value")
                    continue
                await self.broadcast(
                    document_id,
                    {"type": "presence_upsert", "connection": connection},
                )

    async def run(self):
        """Subscribe first, then restore local keys and resync after Redis recovery."""
        while True:
            try:
                async with self.subscriber.pubsub() as subscription:
                    await subscription.subscribe(CHANNEL, EXPIRED_CHANNEL)
                    if subscription.connection is not None:
                        subscription.connection.register_connect_callback(
                            self._subscriber_reconnected
                        )
                    self.fault.clear()
                    self.available = True
                    for viewers in tuple(self.local.values()):
                        for connection in tuple(viewers.values()):
                            async with connection.lock:
                                if connection.active:
                                    await self._write(connection, "JOIN")
                    for document_id in tuple(self.local):
                        await self.broadcast(document_id, await self.snapshot(document_id))
                    listener = asyncio.create_task(self._listen(subscription))
                    fault = asyncio.create_task(self.fault.wait())
                    try:
                        done, _ = await asyncio.wait(
                            (listener, fault), return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            task.result()
                    finally:
                        # Close the blocking Pub/Sub read before canceling its task.
                        if subscription.connection is not None:
                            subscription.connection.deregister_connect_callback(
                                self._subscriber_reconnected
                            )
                        await subscription.aclose()
                        listener.cancel()
                        fault.cancel()
                        await asyncio.gather(listener, fault, return_exceptions=True)
            except RedisError:
                LOGGER.warning("Redis presence unavailable; document editing remains active")
            await self._mark_unavailable()
            await asyncio.sleep(1)
