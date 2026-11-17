"""Content redaction and unscannable-file omissions. Synthetic strings only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grok_bot_backup import handover
from grok_bot_backup.sanitize import redact_structured, redact_text, transfer_regular_file
from grok_bot_backup.sensitive import copy_tree


class RedactionTests(unittest.TestCase):
    def test_nested_json_redacts_secret_keys_and_keeps_max_tokens(self):
        payload = {
            "theme": "dawn",
            "max_tokens": 128,
            "auth": {
                "api_key": "synthetic-api",
                "access_token": "synthetic-access",
                "authorization": "synthetic-authz",
                "password": "synthetic-password",
                "private_key": "synthetic-private",
            },
            "note": "The token parser splits text. max_tokens stays 128.",
        }
        redacted, stats = redact_structured(payload)
        self.assertEqual(redacted["max_tokens"], 128)
        self.assertEqual(redacted["theme"], "dawn")
        self.assertEqual(redacted["note"], payload["note"])
        for key in ("api_key", "access_token", "authorization", "password", "private_key"):
            self.assertEqual(redacted["auth"][key], "<redacted>")
            self.assertIn(key, stats["structured_keys"])
        encoded = json.dumps(redacted)
        self.assertNotIn("synthetic-api", encoded)
        self.assertNotIn("synthetic-access", encoded)
        self.assertNotIn("synthetic-password", encoded)
        self.assertNotIn("synthetic-private", encoded)

    def test_text_assignments_do_not_eat_technical_discussion(self):
        source = "\n".join([
            "def tokenize(text):",
            "    return text.split()",
            "max_tokens = 128",
            "API_KEY=synthetic-assign",
            "access_token = synthetic-access",
            "Authorization: Bearer syntheticbearer",
            "Choose a strong password policy.",
            "We discussed the token parser.",
            "-----BEGIN PRIVATE KEY-----",
            "synthetic-pem-body",
            "-----END PRIVATE KEY-----",
        ])
        redacted, count = redact_text(source)
        self.assertGreater(count, 0)
        self.assertIn("def tokenize(text):", redacted)
        self.assertIn("max_tokens = 128", redacted)
        self.assertIn("Choose a strong password policy.", redacted)
        self.assertIn("token parser", redacted)
        self.assertNotIn("synthetic-assign", redacted)
        self.assertNotIn("synthetic-access", redacted)
        self.assertNotIn("syntheticbearer", redacted)
        self.assertNotIn("synthetic-pem-body", redacted)
        self.assertNotIn("BEGIN PRIVATE KEY", redacted)


class OmissionTests(unittest.TestCase):
    def test_database_binary_oversize_and_encoding_are_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "tree"
            src.mkdir()
            (src / "store.db").write_bytes(b"synthetic-db-bytes")
            (src / "blob.bin").write_bytes(b"\x00synthetic-binary")
            (src / "odd.txt").write_bytes(b"\xff\xfesynthetic-encoding")
            (src / "big.txt").write_text("synthetic-oversize-value " + ("x" * 40), encoding="utf-8")
            (src / "note.md").write_text("max_tokens = 128\n", encoding="utf-8")
            with patch.dict(os.environ, {"SANITIZE_MAX_BYTES": "32", "BACKUP_EXCLUDE_PATHS": ""}):
                report = copy_tree(src, root / "dest", label="fixture")
            dest = root / "dest"
            self.assertFalse((dest / "store.db").exists())
            self.assertFalse((dest / "blob.bin").exists())
            self.assertFalse((dest / "odd.txt").exists())
            self.assertFalse((dest / "big.txt").exists())
            self.assertEqual((dest / "note.md").read_text(encoding="utf-8"), "max_tokens = 128\n")
            reasons = {item["reason"] for item in report["omitted_unscannable"]}
            self.assertIn("unscannable database", reasons)
            self.assertIn("unscannable binary", reasons)
            self.assertIn("unrecognized encoding", reasons)
            self.assertIn("oversize", reasons)
            blob = b"".join(path.read_bytes() for path in dest.rglob("*") if path.is_file())
            self.assertNotIn(b"synthetic-db-bytes", blob)
            self.assertNotIn(b"synthetic-binary", blob)
            self.assertNotIn(b"synthetic-oversize-value", blob)
            joined = json.dumps(report)
            self.assertNotIn("synthetic-db-bytes", joined)
            self.assertNotIn("synthetic-binary", joined)

    def test_transfer_preserves_memory_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "memory-agent.jsonl"
            src.write_text(json.dumps({
                "date": "2026-10-22",
                "tier": "note",
                "scope": "agent",
                "source": "RecallMemory",
                "fact": "Keep this fact. API_KEY=synthetic-memory-value",
            }) + "\n", encoding="utf-8")
            os.utime(src, (1_800_000_000, 1_800_000_000))
            outcome = transfer_regular_file(src, root / "out.jsonl")
            text = (root / "out.jsonl").read_text(encoding="utf-8")
            row = json.loads(text)
            self.assertEqual(row["date"], "2026-10-22")
            self.assertEqual(row["tier"], "note")
            self.assertEqual(row["scope"], "agent")
            self.assertEqual(row["source"], "RecallMemory")
            self.assertIn("Keep this fact.", row["fact"])
            self.assertNotIn("synthetic-memory-value", text)
            self.assertEqual(row["sanitization"], "redacted")
            self.assertEqual(outcome["action"], "redacted")
            self.assertEqual(int((root / "out.jsonl").stat().st_mtime), 1_800_000_000)
            self.assertNotIn("synthetic-memory-value", json.dumps(outcome))


class HandoverGapTests(unittest.TestCase):
    def test_missing_handover_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(handover, "AGENT_DATA", root / "agent-data"):
                report = handover.install_handover(root / "handover", label="raw-handover")
            text = (root / "handover" / "HANDOVER_STATUS.md").read_text(encoding="utf-8")
            self.assertIn("not supplied", text)
            self.assertIn("not a complete handover", text)
            self.assertEqual(report["handover_status"], "not supplied")
            coverage = root / "coverage.md"
            coverage.write_text("# Coverage matrix\n", encoding="utf-8")
            handover.append_coverage(coverage, False)
            self.assertIn("not a complete handover", coverage.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
