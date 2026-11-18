"""Check how committed edits fold into version-history entries."""

import base64
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from services.collaboration.replicated import ReplicatedCollaboration
from services.raft.storage import Entry

START = datetime(2026, 1, 1, tzinfo=UTC)
DOCUMENT = str(uuid4())


class VersionGroupingTest(unittest.TestCase):
    def setUp(self):
        self.app = ReplicatedCollaboration()
        self.app.node = SimpleNamespace(role="follower")
        self.index = 0
        self.body = Doc()
        self.body["default"] = XmlFragment([XmlElement("paragraph")])
        self.apply("create", "owner", 0, title="Doc", update=self.body.get_update())

    def apply(self, kind, actor, seconds, **payload):
        if isinstance(payload.get("update"), bytes):
            payload["update"] = base64.b64encode(payload["update"]).decode()
        self.index += 1
        command = {
            "kind": kind, "actor": actor, "document_id": DOCUMENT, "operation_id": str(uuid4()),
            "time": (START + timedelta(seconds=seconds)).isoformat(), **payload,
        }
        self.app.apply(Entry(self.index, 1, json.dumps(command).encode()))

    def edit(self, actor, seconds, text="x"):
        state = self.body.get_state()
        self.body["default"].children[0].children.append(XmlText(text))
        self.apply("edit", actor, seconds, update=self.body.get_update(state))

    def kinds(self):
        return [(meta.kind, meta.actor_id) for meta, _ in self.app.documents[DOCUMENT].versions]

    def test_rapid_edits_by_one_person_form_one_version(self):
        for second in range(1, 60):
            self.edit("owner", second)
        self.assertEqual(self.kinds(), [("create", "owner"), ("edit", "owner")])
        version = self.app.documents[DOCUMENT].versions[-1][0]
        self.assertEqual(version.timestamp, (START + timedelta(seconds=59)).isoformat())

    def test_pause_author_change_rename_and_long_session_start_new_versions(self):
        self.edit("owner", 1)
        self.edit("owner", 1 + 121)  # idle gap exceeded
        self.app.documents[DOCUMENT].metadata.share_enabled = True
        self.edit("guest", 130)  # different author
        self.apply("rename", "owner", 140, title="Renamed")
        self.edit("owner", 150)  # a rename closes the open edit version
        for second in range(160, 820, 60):
            self.edit("owner", second)  # one session longer than ten minutes
        self.assertEqual(self.kinds(), [
            ("create", "owner"), ("edit", "owner"), ("edit", "owner"), ("edit", "guest"),
            ("rename", "owner"), ("edit", "owner"), ("edit", "owner"),
        ])

    def test_grouped_version_restores_every_folded_edit(self):
        self.edit("owner", 1, "Hello ")
        self.edit("owner", 2, "world")
        restored = Doc()
        restored.apply_update(self.app._version_update(self.app.documents[DOCUMENT], 2))
        restored["default"] = XmlFragment()
        self.assertIn("Hello world", str(restored["default"]))


if __name__ == "__main__":
    unittest.main()
