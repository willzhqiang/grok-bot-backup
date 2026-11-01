"""ISSUE-837: drop routines, reconstructed instructions, coverage, transcripts."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grok_bot_backup import conversations, coverage, memory_drop, virtual_office


AGENT = "00000000-0000-0000-0000-0000000000aa"
OTHER = "00000000-0000-0000-0000-0000000000bb"


def _routines():
    return {
        "routines": [
            {"name": "Hourly learn", "cron": "30 * * * *", "timezone": "Asia/Shanghai",
             "prompt": "Learn and post a short card.", "enabled": True, "source": "platform"},
            {"name": "Morning bookmarks", "cron": "10 7 * * *", "timezone": "Asia/Shanghai",
             "prompt": "Summarize new bookmarks.", "enabled": True, "source": "platform"},
            {"name": "Evening digest", "cron": "0 20 * * *", "timezone": "Asia/Shanghai",
             "prompt": "Summarize the evening list.", "enabled": True, "source": "platform"},
            {"name": "Author check", "cron": "3,13,23,33,43,53 * * * *", "timezone": "Asia/Shanghai",
             "prompt": "Run the author check script.", "enabled": True, "source": "box-cron"},
            {"name": "Bookmark check", "cron": "5 7 * * *", "timezone": "Asia/Shanghai",
             "prompt": "Run the bookmark check script.", "enabled": True, "source": "box-cron"},
        ]
    }


class RoutineDropTests(unittest.TestCase):
    def test_drop_routines_are_copied_and_not_marked_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agent-data" / "agents" / AGENT
            agent.mkdir(parents=True)
            (agent / "profile.json").write_text(json.dumps({
                "name": "Desk Monitor",
                "description": "Watch a feed and send a short card.",
                "instructions": "THIS IS NOT THE SYSTEM PROMPT",
            }), encoding="utf-8")
            (agent / "private" / "feishu.env").parent.mkdir(parents=True)
            (agent / "private" / "feishu.env").write_text(
                "FEISHU_WEBHOOK_URL=https://example.invalid/hook/do-not-copy\n", encoding="utf-8")
            (agent / "quant-notes" / "idea.json").parent.mkdir()
            (agent / "quant-notes" / "idea.json").write_text('{"idea":"sample"}', encoding="utf-8")
            (agent / "standing-rules.md").write_text("Do not place orders.\n", encoding="utf-8")
            drop = root / "memory-drop" / "agents" / AGENT
            drop.mkdir(parents=True)
            (drop / "routines.json").write_text(json.dumps(_routines()), encoding="utf-8")
            (drop / "memory-profile.jsonl").write_text(
                '{"date":"2026-10-22","tier":"profile","scope":"agent","fact":"sample fact"}\n',
                encoding="utf-8")
            dest = root / "assistants"
            with patch.dict(os.environ, {"MEMORY_DROP": str(root / "memory-drop")}):
                record = virtual_office.export_agent(agent, dest, {})
            folder = dest / "Desk Monitor"
            raw = json.loads((folder / "routines" / "routines.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw["routines"]), 5)
            text = (folder / "routines.md").read_text(encoding="utf-8")
            self.assertIn("Hourly learn", text)
            self.assertIn("30 * * * *", text)
            self.assertIn("box-cron", text)
            self.assertIn("platform", text)
            self.assertNotIn("Status: unavailable", text)
            self.assertEqual(record["cells"]["routines"], "ok")
            self.assertIn("3 platform", record["notes"]["routines"])
            self.assertIn("2 box-cron", record["notes"]["routines"])
            instructions = (folder / "instructions.md").read_text(encoding="utf-8")
            self.assertIn("Source: `reconstructed`", instructions)
            self.assertIn("Watch a feed and send a short card.", instructions)
            self.assertIn("Do not place orders.", instructions)
            self.assertNotIn("THIS IS NOT THE SYSTEM PROMPT", instructions)
            meta = json.loads((folder / "raw" / "instructions.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["source"], "reconstructed")
            self.assertFalse(meta["complete"])
            self.assertEqual(meta["coverage"], "unavailable")
            self.assertEqual(record["cells"]["instructions"], "unavailable")
            self.assertEqual(record["cells"]["standing-rules"], "ok")
            self.assertEqual(record["cells"]["business-data-tree"], "ok")
            self.assertTrue((folder / "quant-notes" / "idea.json").is_file())
            secrets = (folder / "ui-only-secrets.md").read_text(encoding="utf-8")
            self.assertIn("unavailable", secrets)
            self.assertIn("FEISHU_WEBHOOK_URL", secrets)
            self.assertNotIn("do-not-copy", secrets)
            self.assertEqual(record["cells"]["ui-only-secrets"], "unavailable")

    def test_no_routines_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agents" / AGENT
            agent.mkdir(parents=True)
            (agent / "profile.json").write_text('{"name":"Quiet","description":"Quiet."}', encoding="utf-8")
            (agent / "automations").mkdir()
            inbox = agent / "automation_completion_inbox"
            inbox.mkdir()
            (inbox / "done.json").write_text(
                '{"name":"inbox-receipt","cron":"0 0 * * *","source":"platform"}', encoding="utf-8")
            with patch.dict(os.environ, {"MEMORY_DROP": str(root / "empty-drop")}):
                record = virtual_office.export_agent(agent, root / "out", {})
            text = (root / "out" / "Quiet" / "routines.md").read_text(encoding="utf-8")
            self.assertIn("unavailable", text)
            self.assertNotIn("inbox-receipt", text)
            self.assertNotIn("0 0 * * *", text)
            self.assertEqual(record["cells"]["routines"], "unavailable")

    def test_crontab_fills_box_cron_when_drop_has_no_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agents" / AGENT
            watch = agent / "private" / "watch"
            watch.mkdir(parents=True)
            (agent / "profile.json").write_text('{"name":"Cron Desk","description":"Desk."}', encoding="utf-8")
            (watch / "crontab.txt").write_text(
                "5 7 * * * /usr/bin/check\n", encoding="utf-8")
            with patch.dict(os.environ, {"MEMORY_DROP": str(root / "empty-drop")}):
                record = virtual_office.export_agent(agent, root / "out", {})
            text = (root / "out" / "Cron Desk" / "routines.md").read_text(encoding="utf-8")
            self.assertIn("box-cron", text)
            self.assertIn("5 7 * * *", text)
            self.assertEqual(record["cells"]["routines"], "ok")


class MemoryIndexTests(unittest.TestCase):
    def test_without_roster_discovered_agents_are_per_assistant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for agent_id, fact in ((AGENT, "one"), (OTHER, "two")):
                directory = root / "agents" / agent_id
                directory.mkdir(parents=True)
                (directory / "memory-agent.jsonl").write_text(
                    json.dumps({"date": "2026-10-22", "tier": "note", "scope": "agent", "fact": fact}) + "\n",
                    encoding="utf-8")
            (root / "agents" / "not-an-agent").mkdir()
            (root / "agents" / "not-an-agent" / "memory-agent.jsonl").write_text(
                '{"fact":"extra"}\n', encoding="utf-8")
            (root / "agents" / AGENT / "routines.json").write_text(
                json.dumps(_routines()), encoding="utf-8")
            with patch.object(memory_drop, "drop_root", return_value=root), \
                 patch.object(memory_drop, "EXPECTED_AGENTS", []), \
                 patch.object(memory_drop, "AGENT_DATA", root / "no-box"):
                dest = root / "office"
                memory_drop.apply_tree(dest, {AGENT: "Desk Monitor", OTHER: "Other Desk"})
                memory_drop.apply_grok(root / "raw")
            index = (dest / "memory-index.md").read_text(encoding="utf-8")
            per = index.split("## Per assistant", 1)[1].split("## Additional", 1)[0]
            self.assertIn("Desk Monitor", per)
            self.assertIn("Other Desk", per)
            self.assertNotIn("not-an-agent", per)
            self.assertIn("routines.json 5", per)
            self.assertIn("`not-an-agent`", index.split("## Additional", 1)[1])
            self.assertNotIn("Desk Monitor", index.split("## Additional", 1)[1])
            self.assertTrue(index.startswith("Memory export: 2/2, missing: none"))
            self.assertTrue((root / "raw" / "agents" / AGENT / "platform-memory" / "routines.json").is_file())
            self.assertFalse((dest / "01-assistants" / "Desk Monitor" / "memory" / "routines.json").exists())


class TranscriptTests(unittest.TestCase):
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute(
            "create table messages (id integer, seq integer, entry_id text, role text, "
            "timestamp_ms integer, body text, agent_id text)")
        db.execute(
            "create table media (id integer, agent_id text, timestamp_ms integer, seq integer, "
            "entry_id text, file_name text, kind text, ext text)")
        db.execute("create table agents (agent_id text)")
        return db

    def _info(self, agent_dir):
        return {
            "id": AGENT, "name": "Desk Monitor", "short": "Desk Monitor",
            "kind": "agent", "dir": agent_dir, "folder": "Desk Monitor",
        }

    def test_jsonl_and_sqlite_and_miss(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agents" / AGENT
            agent.mkdir(parents=True)
            info = self._info(agent)
            conv = root / "conversations"
            cell, _source = conversations.export_tool_transcript(info, conv)
            readme_dir = conv
            # miss: no files yet, export_tool_transcript does not write the README
            self.assertEqual(cell, "unavailable")

            db_path = agent / "store.db"
            conn = sqlite3.connect(db_path)
            conn.execute("create table transcript_entries (id text, entry text)")
            conn.execute(
                "insert into transcript_entries values (?, ?)",
                ("1", json.dumps({"role": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "from-db", "input": {"q": "sample"}}
                ]}})),
            )
            conn.commit()
            conn.close()
            cell, source = conversations.export_tool_transcript(info, conv)
            self.assertEqual(cell, "ok")
            self.assertIn("store.db", source)
            rendered = (conv / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("from-db", rendered)

            jsonl = root / "agent-transcripts" / AGENT / f"{AGENT}.jsonl"
            jsonl.parent.mkdir(parents=True)
            jsonl.write_text(json.dumps({"role": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "from-file", "input": {"q": "file"}}
            ]}}) + "\n{not json\n", encoding="utf-8")
            cell, source = conversations.export_tool_transcript(info, conv)
            self.assertEqual(cell, "ok")
            self.assertIn("agent-transcripts", source)
            rendered = (conv / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("from-file", rendered)
            self.assertNotIn("from-db", rendered)

            jsonl.write_text("{not json\n", encoding="utf-8")
            os.remove(db_path)
            empty = sqlite3.connect(db_path)
            empty.execute("create table transcript_entries (id text, entry text)")
            empty.commit()
            empty.close()
            cell, _source = conversations.export_tool_transcript(info, conv)
            self.assertEqual(cell, "unavailable")
            stats = conversations.export_agent_conversation(self._db(), info, readme_dir, None, 0)
            self.assertEqual(stats["toolcalls"], "unavailable")
            self.assertIn("tool calls: unavailable", (readme_dir / "README.md").read_text(encoding="utf-8"))


class CoverageMatrixTests(unittest.TestCase):
    def test_report_warns_on_unavailable_and_fails_on_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cells = {name: "ok" for name in coverage.DIMENSIONS}
            cells["instructions"] = "unavailable"
            cells["routines"] = "ok"
            rows = [coverage._pack_row(AGENT, "Desk Monitor", cells, {"instructions": "reconstructed"})]
            coverage.write_reports(root, rows)
            text = (root / "coverage.md").read_text(encoding="utf-8")
            self.assertIn("business-data-tree", text)
            self.assertIn("ui-only-secrets", text)
            self.assertIn("standing-rules", text)
            self.assertIn("routines", text)
            self.assertTrue(coverage.report_coverage(root, strict=True, show=True))
            cells["routines"] = "missing"
            coverage.write_reports(root, [coverage._pack_row(AGENT, "Desk Monitor", cells, {})])
            self.assertFalse(coverage.report_coverage(root, strict=True, show=False))


if __name__ == "__main__":
    unittest.main()
