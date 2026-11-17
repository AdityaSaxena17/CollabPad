"""Durable Raft log and hard state for one replica."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    index: int
    term: int
    command: bytes


@dataclass(frozen=True)
class HardState:
    term: int
    voted_for: str | None
    commit_index: int


class RaftStorage:
    """Owns one SQLite connection and serializes changes in transactions."""

    def __init__(self, path: Path, node_id: str):
        self.connection = sqlite3.connect(path, isolation_level="IMMEDIATE")
        try:
            self.connection.execute("PRAGMA journal_mode = WAL").fetchone()
            self.connection.execute("PRAGMA synchronous = FULL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS raft_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    node_id TEXT NOT NULL,
                    term INTEGER NOT NULL DEFAULT 0,
                    voted_for TEXT,
                    commit_index INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS raft_log (
                    log_index INTEGER PRIMARY KEY CHECK (log_index > 0),
                    term INTEGER NOT NULL CHECK (term > 0),
                    command BLOB NOT NULL
                );
            """)
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO raft_state(singleton, node_id) VALUES (1, ?)",
                    (node_id,),
                )
                stored_id, = self.connection.execute("SELECT node_id FROM raft_state").fetchone()
                if stored_id != node_id:
                    raise ValueError("Raft volume belongs to another node.")
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def state(self) -> HardState:
        row = self.connection.execute(
            "SELECT term, voted_for, commit_index FROM raft_state"
        ).fetchone()
        return HardState(*row)

    def record_vote(self, term: int, candidate: str | None) -> None:
        with self.connection:
            state = self.state()
            if term < state.term or (
                term == state.term and state.voted_for is not None and candidate != state.voted_for
            ):
                raise ValueError("Cannot replace a vote in the current or an earlier term.")
            self.connection.execute(
                "UPDATE raft_state SET term = ?, voted_for = ?", (term, candidate)
            )

    def tip(self) -> tuple[int, int]:
        row = self.connection.execute(
            "SELECT log_index, term FROM raft_log ORDER BY log_index DESC LIMIT 1"
        ).fetchone()
        return row or (0, 0)

    def term_at(self, index: int) -> int | None:
        if index == 0:
            return 0
        row = self.connection.execute(
            "SELECT term FROM raft_log WHERE log_index = ?", (index,)
        ).fetchone()
        return row[0] if row else None

    def entries_from(self, index: int, limit: int) -> list[Entry]:
        return [
            Entry(*row)
            for row in self.connection.execute(
                "SELECT log_index, term, command FROM raft_log WHERE log_index >= ? "
                "ORDER BY log_index LIMIT ?",
                (index, limit),
            )
        ]

    def append(self, term: int, command: bytes) -> Entry:
        index = self.tip()[0] + 1
        with self.connection:
            self.connection.execute("INSERT INTO raft_log VALUES (?, ?, ?)", (index, term, command))
        return Entry(index, term, command)

    def accept_entries(
        self,
        previous_index: int,
        previous_term: int,
        entries: Sequence[Entry],
        leader_commit: int,
    ) -> int | None:
        """Return the matched index, or None when the previous entry differs."""
        if self.term_at(previous_index) != previous_term:
            return None

        with self.connection:
            commit_index = self.state().commit_index
            for index, entry in enumerate(entries, previous_index + 1):
                if entry.index != index:
                    raise ValueError("Raft entries must be contiguous.")
                existing_term = self.term_at(index)
                if existing_term == entry.term:
                    continue
                if index <= commit_index:
                    raise ValueError("Cannot replace a committed Raft entry.")
                if existing_term is not None:
                    self.connection.execute("DELETE FROM raft_log WHERE log_index >= ?", (index,))
                self.connection.execute(
                    "INSERT INTO raft_log VALUES (?, ?, ?)", (index, entry.term, entry.command)
                )

            matched = previous_index + len(entries)
            self._advance_commit(min(leader_commit, matched))
            return matched

    def commit(self, index: int) -> None:
        with self.connection:
            if index > self.tip()[0]:
                raise ValueError("Cannot commit beyond the end of the Raft log.")
            self._advance_commit(index)

    def _advance_commit(self, index: int) -> None:
        self.connection.execute(
            "UPDATE raft_state SET commit_index = ? WHERE commit_index < ?", (index, index)
        )
