"""Run elections and replicate a fixed-membership Raft log over gRPC."""

import asyncio
import logging
import random
from collections.abc import Callable

import grpc

from services.proto import raft_pb2 as pb
from services.proto import raft_pb2_grpc as rpc
from services.raft.storage import Entry, RaftStorage

LOGGER = logging.getLogger(__name__)
HEARTBEAT = 0.1
PEER_TIMEOUT = 0.25
ELECTION_TIMEOUT = (0.75, 1.5)


class LeaderUnavailable(Exception):
    """The request needs an elected leader with a quorum."""


class RaftNode(rpc.RaftServiceServicer):
    def __init__(
        self, node_id: str, peers: dict[str, str], storage: RaftStorage,
        apply: Callable[[Entry], None],
    ):
        if node_id in peers:
            raise ValueError("Raft peers must exclude this node.")
        self.node_id = node_id
        self.storage = storage
        self.apply = apply
        self.channels = {
            peer_id: grpc.aio.insecure_channel(target) for peer_id, target in peers.items()
        }
        self.peers = {
            peer_id: rpc.RaftServiceStub(channel) for peer_id, channel in self.channels.items()
        }
        self.quorum = (len(peers) + 1) // 2 + 1
        self.role = "follower"
        self.leader_id = ""
        self.applied = 0
        self.match: dict[str, int] = {}
        self.next_index: dict[str, int] = {}
        self.changed = asyncio.Condition()
        self.wake = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self._reset_deadline()
        self.task: asyncio.Task | None = None

    def _reset_deadline(self):
        self.deadline = self.loop.time() + random.uniform(*ELECTION_TIMEOUT)

    def _follow(self, term: int, leader_id: str = ""):
        if term > self.storage.state().term:
            self.storage.record_vote(term, None)
        self.role = "follower"
        self.leader_id = leader_id
        self._reset_deadline()
        self.changed.notify_all()

    def _apply_committed(self):
        commit = self.storage.state().commit_index
        while self.applied < commit:
            for entry in self.storage.entries_from(self.applied + 1, 16):
                if entry.index > commit:
                    break
                if entry.command:
                    self.apply(entry)
                self.applied = entry.index
        self.changed.notify_all()

    async def start(self):
        async with self.changed:
            self._apply_committed()
        self.task = asyncio.create_task(self._run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.gather(*(channel.close() for channel in self.channels.values()))

    async def RequestVote(self, request, context):
        async with self.changed:
            if request.candidate_id not in self.peers:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Unknown Raft candidate.")
            state = self.storage.state()
            if request.term > state.term:
                self._follow(request.term)
                state = self.storage.state()
            index, term = self.storage.tip()
            granted = (
                state.term == request.term
                and state.voted_for in (None, request.candidate_id)
                and (request.last_log_term, request.last_log_index) >= (term, index)
            )
            if granted:
                self.storage.record_vote(request.term, request.candidate_id)
                self._reset_deadline()
            return pb.VoteReply(term=state.term, granted=granted)

    async def AppendEntries(self, request, context):
        async with self.changed:
            if request.leader_id not in self.peers:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Unknown Raft leader.")
            current = self.storage.state().term
            if request.term < current:
                return pb.AppendReply(term=current)
            self._follow(request.term, request.leader_id)
            try:
                matched = self.storage.accept_entries(
                    request.previous_index, request.previous_term,
                    [Entry(entry.index, entry.term, entry.command) for entry in request.entries],
                    request.leader_commit,
                )
            except ValueError as error:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
            self._apply_committed()
            return pb.AppendReply(
                term=request.term, success=matched is not None, match_index=matched or 0
            )

    async def GetStatus(self, request, context):
        async with self.changed:
            state = self.storage.state()
            return pb.StatusReply(
                node_id=self.node_id, role=self.role, term=state.term, leader_id=self.leader_id,
                commit_index=state.commit_index, applied_index=self.applied,
            )

    async def propose(self, command: bytes, timeout: float = 3) -> int:
        """Wait for local application of the majority-committed entry."""
        async with self.changed:
            if self.role != "leader":
                raise LeaderUnavailable()
            term = self.storage.state().term
            entry = self.storage.append(term, command)
            self.wake.set()
            try:
                async with asyncio.timeout(timeout):
                    await self.changed.wait_for(
                        lambda: self.applied >= entry.index
                        or self.role != "leader" or self.storage.state().term != term
                    )
            except TimeoutError as error:
                raise LeaderUnavailable() from error
            if self.role != "leader" or self.storage.state().term != term:
                raise LeaderUnavailable()
            return entry.index

    async def _elect(self):
        async with self.changed:
            if self.role == "leader" or self.loop.time() < self.deadline:
                return
            term = self.storage.state().term + 1
            self.storage.record_vote(term, self.node_id)
            self.role = "candidate"
            self.leader_id = ""
            self._reset_deadline()
            index, log_term = self.storage.tip()
            request = pb.VoteRequest(
                candidate_id=self.node_id, term=term, last_log_index=index, last_log_term=log_term
            )
        replies = await asyncio.gather(*(self._vote(peer, request) for peer in self.peers.values()))
        async with self.changed:
            if self.role != "candidate" or self.storage.state().term != term:
                return
            if 1 + sum(replies) < self.quorum:
                return
            self.role = "leader"
            self.leader_id = self.node_id
            self.next_index = dict.fromkeys(self.peers, self.storage.tip()[0] + 1)
            self.match = dict.fromkeys(self.peers, 0)
            self.storage.append(term, b"")
            self._reset_deadline()
            LOGGER.info("%s elected leader in term %d", self.node_id, term)
            self.wake.set()

    async def _vote(self, peer, request):
        try:
            reply = await peer.RequestVote(request, timeout=PEER_TIMEOUT)
        except grpc.aio.AioRpcError:
            return False
        async with self.changed:
            if reply.term > self.storage.state().term:
                self._follow(reply.term)
            return reply.term == request.term and reply.granted

    async def _replicate(self, peer_id, peer, term):
        async with self.changed:
            if self.role != "leader" or self.storage.state().term != term:
                return False
            start = self.next_index[peer_id]
            entries = self.storage.entries_from(start, 1)
            request = pb.AppendRequest(
                leader_id=self.node_id, term=term, previous_index=start - 1,
                previous_term=self.storage.term_at(start - 1),
                leader_commit=self.storage.state().commit_index,
                entries=[pb.LogEntry(index=e.index, term=e.term, command=e.command) for e in entries],
            )
        try:
            reply = await peer.AppendEntries(request, timeout=PEER_TIMEOUT)
        except grpc.aio.AioRpcError:
            return False
        async with self.changed:
            if reply.term > self.storage.state().term:
                self._follow(reply.term)
            if self.role != "leader" or self.storage.state().term != term:
                return False
            if reply.success:
                self.match[peer_id] = reply.match_index
                self.next_index[peer_id] = reply.match_index + 1
                if reply.match_index < self.storage.tip()[0]:
                    self.wake.set()
            else:
                self.next_index[peer_id] = max(1, start - 1)
                self.wake.set()
            return reply.term == term

    async def _replication_round(self):
        term = self.storage.state().term
        replies = await asyncio.gather(
            *(self._replicate(peer_id, peer, term) for peer_id, peer in self.peers.items())
        )
        async with self.changed:
            if self.role != "leader" or self.storage.state().term != term:
                return
            if 1 + sum(replies) >= self.quorum:
                self._reset_deadline()
            elif self.loop.time() >= self.deadline:
                self._follow(term)
                return
            replicated = sorted([self.storage.tip()[0], *self.match.values()], reverse=True)
            index = replicated[self.quorum - 1]
            if index > self.storage.state().commit_index and self.storage.term_at(index) == term:
                self.storage.commit(index)
                self._apply_committed()
                self.wake.set()

    async def _run(self):
        while True:
            self.wake.clear()
            if self.role == "leader":
                await self._replication_round()
            else:
                await self._elect()
            try:
                await asyncio.wait_for(self.wake.wait(), HEARTBEAT)
            except TimeoutError:
                pass
