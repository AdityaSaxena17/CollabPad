"""Exercise durable votes, replication, and conflict repair."""

import tempfile
import unittest
from pathlib import Path

from services.raft.storage import Entry, HardState, RaftStorage


class RaftStorageTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "raft.sqlite3"
        self.storage = RaftStorage(self.path, "a")
        self.addCleanup(self.storage.close)

    def test_restart_preserves_vote_and_committed_entries(self):
        self.storage.record_vote(1, "b")
        entry = self.storage.append(1, b"create")
        self.storage.commit(entry.index)
        self.storage.close()

        reopened = RaftStorage(self.path, "a")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.state(), HardState(1, "b", 1))
        self.assertEqual(reopened.entries_from(1, 10), [entry])
        with self.assertRaises(ValueError):
            reopened.record_vote(1, "c")
        reopened.record_vote(2, "c")
        self.assertEqual(reopened.state(), HardState(2, "c", 1))

    def test_append_replaces_uncommitted_conflicting_suffix(self):
        first = self.storage.append(1, b"committed")
        self.storage.commit(first.index)
        self.storage.append(1, b"abandoned")
        self.storage.append(1, b"also abandoned")

        new = Entry(2, 2, b"replacement")
        self.assertIsNone(self.storage.accept_entries(1, 9, [new], 2))
        self.assertEqual(self.storage.tip(), (3, 1))
        self.assertEqual(self.storage.accept_entries(1, 1, [new], 10), 2)
        self.assertEqual(self.storage.entries_from(1, 10), [first, new])
        self.assertEqual(self.storage.state().commit_index, 2)

        with self.assertRaises(ValueError):
            self.storage.accept_entries(0, 0, [Entry(1, 3, b"invalid")], 1)
        self.assertEqual(self.storage.entries_from(1, 10), [first, new])

    def test_failed_append_rolls_back_suffix_replacement(self):
        original = self.storage.append(1, b"original")
        with self.assertRaises(ValueError):
            self.storage.accept_entries(
                0, 0, [Entry(1, 2, b"replacement"), Entry(3, 2, b"gap")], 0
            )
        self.assertEqual(self.storage.entries_from(1, 10), [original])

    def test_empty_heartbeat_only_commits_its_matched_prefix(self):
        self.storage.append(1, b"matched")
        self.storage.append(1, b"unmatched")
        self.assertEqual(self.storage.accept_entries(1, 1, [], 2), 1)
        self.assertEqual(self.storage.state().commit_index, 1)
        self.assertEqual(self.storage.tip(), (2, 1))

    def test_volume_cannot_be_reused_by_another_replica(self):
        with self.assertRaisesRegex(ValueError, "another node"):
            RaftStorage(self.path, "b")
