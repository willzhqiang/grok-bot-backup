"""Synthetic credential-filter regressions. No live agent or private backup."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

from grok_bot_backup import coverage, verify
from grok_bot_backup.sensitive import (
    Policy,
    directory_kind,
    exclusion_reason,
    file_kind,
    redact_exact_secret_keys,
)
from grok_bot_backup.workspace import copy_workspace
from grok_bot_backup import virtual_office


AGENT_ID = "00000000-0000-0000-0000-0000000000c1"
SAFE_NOTE = "SAFE_PRIVATE_NOTE ordinary handover"
SAFE_TOKEN_SOURCE = "def tokenize(text):\n    return text.split()\n"
SAFE_SESSION = "Meeting session notes for the handover.\n"
SAFE_MEMORY = "Ordinary memory about design token names in the theme."
SAFE_CHAT = "Please review the token parser notes."
SAFE_ORG = "SAFE_ORG_DB ordinary platform database"
SAFE_KEEP = "SAFE_CUSTOM_KEEP ordinary file"


def _stored_zip(path: Path, member: str, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(member, payload)


def _plain_tar(path: Path, member: str, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = payload.encode("utf-8")
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        import io as _io
        archive.addfile(info, _io.BytesIO(data))


def _gzip_tar(path: Path, member: str, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = payload.encode("utf-8")
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        import io as _io
        archive.addfile(info, _io.BytesIO(data))


class NameRuleTests(unittest.TestCase):
    def test_exact_names_do_not_use_substrings(self):
        kept = (
            "token_parser.py", "tokenizer.py", "design-tokens.css",
            "session-notes.md", "environment.md", "max_tokens.md",
            "store.db", "store.db-wal", "search-index.db", "readme.md",
        )
        for name in kept:
            with self.subTest(name=name):
                self.assertIsNone(file_kind(name))
                self.assertIsNone(exclusion_reason((name,), is_dir=False))
        self.assertEqual(file_kind(".env"), "env file")
        self.assertEqual(file_kind(".env.local"), "env file")
        self.assertEqual(file_kind("feishu.env"), "env file")
        self.assertEqual(file_kind("id_rsa"), "private key")
        self.assertEqual(file_kind("archive.pem"), "private key")
        self.assertEqual(file_kind("cookies.sqlite"), "authentication database")
        self.assertEqual(file_kind("cookies.sqlite-wal"), "authentication database")
        self.assertEqual(file_kind("Login Data"), "authentication database")
        self.assertEqual(file_kind("auth.db"), "authentication database")
        self.assertEqual(file_kind("box-files.tar.gz"), "opaque archive")
        self.assertEqual(file_kind("nested.zip"), "opaque archive")
        self.assertEqual(file_kind("notes.tar"), "opaque archive")
        self.assertEqual(file_kind("credential.txt"), "credential file")
        self.assertEqual(file_kind("token.json"), "token store")
        self.assertEqual(file_kind("session.json"), "session store")
        self.assertEqual(file_kind("cookies.txt"), "cookie store")
        self.assertEqual(directory_kind("secrets"), "credential location")
        self.assertEqual(directory_kind(".ssh"), "credential location")
        self.assertIsNone(directory_kind("src"))
        self.assertIsNone(directory_kind("session-notes"))
        blocked = exclusion_reason(("private", "backup_pre", ".env"), is_dir=False)
        self.assertEqual(blocked, "env file")
        self.assertIsNone(exclusion_reason(("src", "token_parser.py"), is_dir=False))
        self.assertEqual(
            exclusion_reason(("sessions", "readme.md"), is_dir=False),
            "credential location",
        )

    def test_opt_in_flags_are_independent(self):
        allow_files = Policy(include_credential_files=True)
        allow_db = Policy(include_auth_databases=True)
        allow_archives = Policy(include_opaque_archives=True)
        self.assertIsNone(exclusion_reason((".env",), is_dir=False, policy=allow_files))
        self.assertEqual(
            exclusion_reason(("cookies.sqlite",), is_dir=False, policy=allow_files),
            "authentication database",
        )
        self.assertEqual(
            exclusion_reason(("nested.zip",), is_dir=False, policy=allow_files),
            "opaque archive",
        )
        self.assertIsNone(exclusion_reason(("auth.db",), is_dir=False, policy=allow_db))
        self.assertEqual(exclusion_reason((".env",), is_dir=False, policy=allow_db), "env file")
        self.assertIsNone(exclusion_reason(("box-files.tar.gz",), is_dir=False, policy=allow_archives))
        self.assertEqual(exclusion_reason(("id_rsa",), is_dir=False, policy=allow_archives), "private key")

    def test_invalid_flag_fails_closed(self):
        with patch.dict(os.environ, {"INCLUDE_CREDENTIAL_FILES": "yes"}):
            with self.assertRaises(ValueError):
                exclusion_reason((".env",), is_dir=False)

    def test_redact_exact_keys_keeps_ordinary_token_settings(self):
        redacted = redact_exact_secret_keys({
            "theme": "dawn",
            "max_tokens": 128,
            "api_key": "should-not-survive",
            "nested": {"password": "should-not-survive", "sessionTimeout": 30},
        })
        self.assertEqual(redacted["theme"], "dawn")
        self.assertEqual(redacted["max_tokens"], 128)
        self.assertEqual(redacted["api_key"], "<redacted>")
        self.assertEqual(redacted["nested"]["password"], "<redacted>")
        self.assertEqual(redacted["nested"]["sessionTimeout"], 30)
        self.assertNotIn("should-not-survive", json.dumps(redacted))


class CopyRuleTests(unittest.TestCase):
    def test_workspace_keeps_token_source_and_drops_env_and_archive(self):
        token = uuid.uuid4().hex
        secret = f"SYNTHETIC_SECRET_UNIT_{token}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "workspace"
            (src / "src").mkdir(parents=True)
            (src / "src" / "token_parser.py").write_text(SAFE_TOKEN_SOURCE, encoding="utf-8")
            (src / "session-notes.md").write_text(SAFE_SESSION, encoding="utf-8")
            (src / "app").mkdir()
            (src / "app" / ".env").write_text(f"TOKEN={secret}\n", encoding="utf-8")
            _stored_zip(src / "migration" / "nested.zip", "hidden.env", secret)
            (src / "exports").mkdir()
            env = {
                "INCLUDE_BULKY_DIRS": "0",
                "INCLUDE_CREDENTIAL_FILES": "0",
                "INCLUDE_AUTH_DATABASES": "0",
                "INCLUDE_OPAQUE_ARCHIVES": "0",
                "BACKUP_EXCLUDE_PATHS": "",
            }
            with patch.dict(os.environ, env):
                report = copy_workspace(src, root / "dest", src / "exports")
            dest = root / "dest"
            self.assertEqual((dest / "src" / "token_parser.py").read_text(encoding="utf-8"), SAFE_TOKEN_SOURCE)
            self.assertTrue((dest / "session-notes.md").is_file())
            self.assertFalse((dest / "app" / ".env").exists())
            self.assertFalse((dest / "migration" / "nested.zip").exists())
            blob = b"".join(path.read_bytes() for path in dest.rglob("*") if path.is_file() and not path.is_symlink())
            self.assertNotIn(secret.encode(), blob)
            reasons = " ".join(report["skipped"])
            self.assertIn("env file", reasons)
            self.assertIn("opaque archive", reasons)

    def test_configured_exclude_matches_nested_agent_path(self):
        from grok_bot_backup.sensitive import copy_tree
        token = uuid.uuid4().hex
        secret = f"SYNTHETIC_SECRET_SKIP_{token}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "agents"
            (src / AGENT_ID / "custom-skip").mkdir(parents=True)
            (src / AGENT_ID / "custom-skip" / "plain.txt").write_text(secret, encoding="utf-8")
            (src / AGENT_ID / "custom-keep").mkdir()
            (src / AGENT_ID / "custom-keep" / "plain.txt").write_text(SAFE_KEEP, encoding="utf-8")
            with patch.dict(os.environ, {"BACKUP_EXCLUDE_PATHS": "custom-skip", "INCLUDE_CREDENTIAL_FILES": "0"}):
                report = copy_tree(src, root / "dest", label="agent-data/agents")
            dest = root / "dest"
            self.assertFalse((dest / AGENT_ID / "custom-skip").exists())
            self.assertEqual((dest / AGENT_ID / "custom-keep" / "plain.txt").read_text(encoding="utf-8"), SAFE_KEEP)
            self.assertTrue(any(item["reason"] == "configured exclusion" for item in report["excluded"]))
            blob = b"".join(path.read_bytes() for path in dest.rglob("*") if path.is_file())
            self.assertNotIn(secret.encode(), blob)

    def test_symlink_escape_is_not_followed(self):
        from grok_bot_backup.sensitive import copy_tree
        token = uuid.uuid4().hex
        secret = f"SYNTHETIC_SECRET_LINK_{token}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "tree"
            (src / "notes").mkdir(parents=True)
            (src / "notes" / "memo.md").write_text("safe memo\n", encoding="utf-8")
            outside = root / "outside-secret.txt"
            outside.write_text(secret, encoding="utf-8")
            os.symlink(outside, src / "escape")
            os.symlink("memo.md", src / "notes" / "alias.md")
            os.symlink("../../outside-secret.txt", src / "notes" / "rel-escape")
            with patch.dict(os.environ, {"INCLUDE_CREDENTIAL_FILES": "0", "BACKUP_EXCLUDE_PATHS": ""}):
                copy_tree(src, root / "dest")
            dest = root / "dest"
            self.assertFalse((dest / "escape").exists())
            self.assertFalse((dest / "notes" / "rel-escape").exists())
            self.assertTrue((dest / "notes" / "alias.md").is_symlink())
            self.assertEqual(os.readlink(dest / "notes" / "alias.md"), "memo.md")
            blob = b"".join(path.read_bytes() for path in dest.rglob("*") if path.is_file() and not path.is_symlink())
            self.assertNotIn(secret.encode(), blob)

    def test_profile_markdown_redacts_secret_keys(self):
        token = uuid.uuid4().hex
        secret = f"SYNTHETIC_SECRET_SETTINGS_{token}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agents" / AGENT_ID
            agent.mkdir(parents=True)
            (agent / "profile.json").write_text(json.dumps({
                "name": "Test assistant",
                "description": "Keeps handover notes.",
            }), encoding="utf-8")
            (agent / "settings.json").write_text(json.dumps({
                "theme": "dawn",
                "max_tokens": 128,
                "api_key": secret,
            }), encoding="utf-8")
            with patch.dict(os.environ, {"MEMORY_DROP": str(root / "empty-drop"), "INCLUDE_CREDENTIAL_FILES": "0"}):
                virtual_office.export_agent(agent, root / "out", {})
            profile = (root / "out" / "Test assistant" / "profile.md").read_text(encoding="utf-8")
            self.assertIn("dawn", profile)
            self.assertIn("128", profile)
            self.assertIn("<redacted>", profile)
            self.assertNotIn(secret, profile)


class ExportFilterTests(unittest.TestCase):
    def _build(self, root: Path) -> dict[str, str]:
        token = uuid.uuid4().hex
        secrets = {
            "env": f"SYNTHETIC_SECRET_ENV_{token}",
            "key": f"SYNTHETIC_SECRET_KEY_{token}",
            "cookie": f"SYNTHETIC_SECRET_COOKIE_{token}",
            "authdb": f"SYNTHETIC_SECRET_AUTHDB_{token}",
            "tar": f"SYNTHETIC_SECRET_TAR_{token}",
            "zip": f"SYNTHETIC_SECRET_ZIP_{token}",
            "tgz": f"SYNTHETIC_SECRET_TGZ_{token}",
            "link": f"SYNTHETIC_SECRET_LINK_{token}",
            "ws": f"SYNTHETIC_SECRET_WS_{token}",
            "project": f"SYNTHETIC_SECRET_PROJECT_{token}",
            "skip": f"SYNTHETIC_SECRET_SKIP_{token}",
            "session": f"SYNTHETIC_SECRET_SESSION_{token}",
            "sub": f"SYNTHETIC_SECRET_SUB_{token}",
        }
        data = root / "agent-data"
        agent = data / "agents" / AGENT_ID
        backup = agent / "private" / "backup_pre"
        backup.mkdir(parents=True)
        (backup / ".env").write_text(f"API_TOKEN={secrets['env']}\n", encoding="utf-8")
        (backup / "id_rsa").write_text(secrets["key"], encoding="utf-8")
        (backup / "cookies.sqlite").write_bytes(secrets["cookie"].encode())
        (agent / "auth.db").write_bytes(secrets["authdb"].encode())
        (agent / "private" / "note.txt").write_text(SAFE_NOTE, encoding="utf-8")
        (agent / "notes").mkdir()
        (agent / "notes" / "token-handling.md").write_text(
            "Ordinary note about the token parser.\n", encoding="utf-8")
        (agent / "sessions").mkdir()
        (agent / "sessions" / "sessionstore.json").write_text(secrets["session"], encoding="utf-8")
        (agent / "custom-skip").mkdir()
        (agent / "custom-skip" / "plain.txt").write_text(secrets["skip"], encoding="utf-8")
        (agent / "custom-keep").mkdir()
        (agent / "custom-keep" / "plain.txt").write_text(SAFE_KEEP, encoding="utf-8")
        _plain_tar(agent / "migration" / "box-files.tar", "hidden.env", secrets["tar"])
        _stored_zip(agent / "migration" / "nested.zip", "hidden.env", secrets["zip"])
        _gzip_tar(agent / "migration" / "box-files.tar.gz", "hidden.env", secrets["tgz"])
        (agent / "migration" / "readme.md").write_text("ordinary migration note\n", encoding="utf-8")
        (agent / "profile.json").write_text(json.dumps({
            "name": "Test assistant",
            "description": "Keeps handover notes.",
        }), encoding="utf-8")
        (agent / "settings.json").write_text(json.dumps({
            "theme": "dawn",
            "max_tokens": 128,
        }), encoding="utf-8")
        (agent / "standing-rules.md").write_text("Keep ordinary handover notes.\n", encoding="utf-8")
        outside = root / "outside-secret.txt"
        outside.write_text(secrets["link"], encoding="utf-8")
        os.symlink(outside, agent / "private" / "escape")
        os.symlink("backup_pre/.env", agent / "private" / "link-to-env")
        os.symlink("token-handling.md", agent / "notes" / "alias.md")
        (data / "agents" / "organization").mkdir(parents=True)
        (data / "agents" / "organization" / "store.db").write_text(SAFE_ORG, encoding="utf-8")
        (data / "agents" / "active-agent.json").write_text('{"active":true}', encoding="utf-8")
        (data / "projects" / "demo").mkdir(parents=True)
        (data / "projects" / "demo" / "readme.md").write_text("project readme\n", encoding="utf-8")
        (data / "projects" / "demo" / ".env").write_text(secrets["project"], encoding="utf-8")
        (data / "workflows" / "skill").mkdir(parents=True)
        (data / "workflows" / "skill" / "SKILL.md").write_text("ordinary skill\n", encoding="utf-8")
        (data / "user-memory").mkdir()
        (data / "user-memory" / "memory.md").write_text("ordinary user memory\n", encoding="utf-8")
        transcript = data / "agent-transcripts" / AGENT_ID
        transcript.mkdir(parents=True)
        (transcript / f"{AGENT_ID}.jsonl").write_text('{"role":"assistant","text":"token note"}\n', encoding="utf-8")
        (transcript / ".env").write_text(secrets["env"], encoding="utf-8")
        sub = data / "agent-transcripts" / "sand-subagent-demo"
        sub.mkdir()
        (sub / ".env").write_text(secrets["sub"], encoding="utf-8")

        workspace = root / "workspace"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "token_parser.py").write_text(SAFE_TOKEN_SOURCE, encoding="utf-8")
        (workspace / "session-notes.md").write_text(SAFE_SESSION, encoding="utf-8")
        (workspace / "app").mkdir()
        (workspace / "app" / ".env").write_text(secrets["ws"], encoding="utf-8")
        (workspace / "custom-skip").mkdir()
        (workspace / "custom-skip" / "plain.txt").write_text(secrets["skip"], encoding="utf-8")
        _stored_zip(workspace / "docs" / "migration" / "nested.zip", "hidden.env", secrets["zip"])
        os.symlink(outside, workspace / "escape")
        (workspace / "exports").mkdir()

        conn = sqlite3.connect(data / "search-index.db")
        conn.execute("create table agents (agent_id text)")
        conn.execute("""create table messages (
            id integer primary key, agent_id text, seq integer, entry_id text,
            role text, timestamp_ms integer, body text
        )""")
        conn.execute("""create table media (
            id integer primary key, agent_id text, seq integer, entry_id text,
            timestamp_ms integer, file_name text, kind text, ext text
        )""")
        conn.execute("insert into agents (agent_id) values (?)", (AGENT_ID,))
        conn.execute(
            "insert into messages (agent_id, seq, entry_id, role, timestamp_ms, body)"
            " values (?, 1, 'msg-1', 'user', 1792600000000, ?)",
            (AGENT_ID, SAFE_CHAT),
        )
        conn.commit()
        conn.close()

        drop = root / "memory-drop" / "agents" / AGENT_ID
        drop.mkdir(parents=True)
        (drop / "memory-agent.jsonl").write_text(json.dumps({
            "date": "2026-10-22",
            "tier": "note",
            "scope": "agent",
            "source": "RecallMemory",
            "fact": SAFE_MEMORY,
        }) + "\n", encoding="utf-8")
        return secrets

    def _assert_absent(self, folder: Path, archive: Path, logs: str, secrets: dict[str, str]) -> None:
        payloads = [value.encode("utf-8") for value in secrets.values()]
        export_root = folder.resolve()
        for path in folder.rglob("*"):
            if path.is_symlink():
                link_text = os.readlink(path)
                for secret in secrets.values():
                    self.assertNotIn(secret, link_text)
                try:
                    path.resolve().relative_to(export_root)
                except ValueError:
                    self.fail(f"symlink leaves the export: {path}")
                continue
            if path.is_file():
                data = path.read_bytes()
                for payload in payloads:
                    self.assertNotIn(payload, data, path)
        raw = archive.read_bytes()
        for payload in payloads:
            self.assertNotIn(payload, raw)
            self.assertNotIn(payload.decode("utf-8"), logs)
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            for info in zipped.infolist():
                data = zipped.read(info)
                for payload in payloads:
                    self.assertNotIn(payload, data, info.filename)
                    self.assertNotIn(payload.decode("utf-8"), info.filename)

    def test_raw_and_virtual_office_exports_omit_synthetic_secrets(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._build(root)
            output = root / "workspace" / "exports"
            env = os.environ.copy()
            env.update({
                "AGENT_DATA": str(root / "agent-data"),
                "WORKSPACE": str(root / "workspace"),
                "OUT_ROOT": str(output),
                "MEMORY_DROP": str(root / "memory-drop"),
                "BACKUP_EXCLUDE_PATHS": "custom-skip",
                "INCLUDE_BULKY_DIRS": "0",
                "INCLUDE_SUBAGENTS": "0",
                "INCLUDE_CREDENTIAL_FILES": "0",
                "INCLUDE_AUTH_DATABASES": "0",
                "INCLUDE_OPAQUE_ARCHIVES": "0",
            })
            raw = subprocess.run(
                [str(repo / "bin" / "export-grok-bot-backup.sh")],
                cwd=repo, env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(raw.returncode, 0, raw.stdout + "\n" + raw.stderr)
            folder = next(path for path in output.glob("grok-bot-export-*") if path.is_dir())
            archive = folder.with_suffix(".zip")
            logs = raw.stdout + raw.stderr
            self._assert_absent(folder, archive, logs, secrets)
            self.assertEqual((folder / "agents" / AGENT_ID / "private" / "note.txt").read_text(encoding="utf-8"), SAFE_NOTE)
            self.assertIn("token parser", (folder / "agents" / AGENT_ID / "notes" / "token-handling.md").read_text(encoding="utf-8"))
            self.assertEqual((folder / "agents" / AGENT_ID / "notes" / "alias.md").is_symlink(), True)
            self.assertEqual((folder / "workspace" / "src" / "token_parser.py").read_text(encoding="utf-8"), SAFE_TOKEN_SOURCE)
            self.assertIn("handover", (folder / "workspace" / "session-notes.md").read_text(encoding="utf-8"))
            settings = json.loads((folder / "agents" / AGENT_ID / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["theme"], "dawn")
            self.assertEqual(settings["max_tokens"], 128)
            self.assertEqual((folder / "agents" / "organization" / "store.db").read_text(encoding="utf-8"), SAFE_ORG)
            self.assertEqual((folder / "agents" / AGENT_ID / "custom-keep" / "plain.txt").read_text(encoding="utf-8"), SAFE_KEEP)
            conversation_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (folder / "conversations").rglob("*.md") if path.is_file() and not path.is_symlink()
            )
            self.assertIn(SAFE_CHAT, conversation_text)
            memory = (folder / "agents" / AGENT_ID / "platform-memory" / "memory-agent.jsonl").read_text(encoding="utf-8")
            self.assertIn(SAFE_MEMORY, memory)
            report = json.loads((folder / "meta" / "exclusion-report.json").read_text(encoding="utf-8"))
            reasons = {item["reason"] for item in report["excluded"]}
            self.assertIn("env file", reasons)
            self.assertIn("private key", reasons)
            self.assertIn("authentication database", reasons)
            self.assertIn("opaque archive", reasons)
            self.assertIn("configured exclusion", reasons)
            self.assertTrue(any("symlink" in reason for reason in reasons))
            self.assertIn("missing=0 is not full platform availability", "\n".join(report["limitations"]))
            manifest = (folder / "meta" / "MANIFEST.json").read_text(encoding="utf-8")
            self.assertIn("missing=0 is not full platform availability", manifest)
            for secret in secrets.values():
                self.assertNotIn(secret, manifest)
                self.assertNotIn(secret, json.dumps(report))
            with zipfile.ZipFile(archive) as zipped:
                names = zipped.namelist()
            joined = "\n".join(names)
            self.assertNotIn("backup_pre/.env", joined)
            self.assertNotIn("id_rsa", joined)
            self.assertNotIn("cookies.sqlite", joined)
            self.assertNotIn("auth.db", joined)
            self.assertNotIn("box-files.tar", joined)
            self.assertNotIn("nested.zip", joined)
            self.assertNotIn("sessionstore.json", joined)

            verified = subprocess.run(
                [str(repo / "bin" / "verify-export.sh"), str(folder), "--archive", str(archive), "--strict"],
                cwd=repo, env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout + "\n" + verified.stderr)
            self.assertIn("RESULT OK", verified.stdout)
            self.assertIn("missing=0 is not full platform availability", verified.stdout)
            for secret in secrets.values():
                self.assertNotIn(secret, verified.stdout + verified.stderr)

            readable = subprocess.run(
                [str(repo / "bin" / "export-virtual-office.sh")],
                cwd=repo, env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(readable.returncode, 0, readable.stdout + "\n" + readable.stderr)
            human = next(path for path in output.glob("virtual-office-*") if path.is_dir())
            human_zip = human.with_suffix(".zip")
            human_logs = readable.stdout + readable.stderr
            self._assert_absent(human, human_zip, human_logs, secrets)
            assistant = human / "01-assistants" / "Test assistant"
            self.assertEqual((assistant / "private" / "note.txt").read_text(encoding="utf-8"), SAFE_NOTE)
            self.assertIn("token parser", (assistant / "notes-and-profiles" / "token-handling.md").read_text(encoding="utf-8"))
            profile = (assistant / "profile.md").read_text(encoding="utf-8")
            self.assertIn("dawn", profile)
            self.assertIn("128", profile)
            self.assertIn(SAFE_MEMORY, (assistant / "memory" / "memory-agent.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                (human / "00-shared" / "workspace-outputs" / "src" / "token_parser.py").read_text(encoding="utf-8"),
                SAFE_TOKEN_SOURCE,
            )
            self.assertIn("handover", (human / "00-shared" / "workspace-outputs" / "session-notes.md").read_text(encoding="utf-8"))
            self.assertIn(SAFE_CHAT, "\n".join(
                path.read_text(encoding="utf-8")
                for path in (assistant / "conversations").rglob("*.md") if path.is_file()
            ))
            human_report = json.loads((human / "exclusion-report.json").read_text(encoding="utf-8"))
            human_reasons = {item["reason"] for item in human_report["excluded"]}
            self.assertIn("env file", human_reasons)
            self.assertIn("opaque archive", human_reasons)
            self.assertIn("missing=0 is not full platform availability", (human / "coverage.md").read_text(encoding="utf-8"))
            for secret in secrets.values():
                self.assertNotIn(secret, json.dumps(human_report))

            human_verified = subprocess.run(
                [str(repo / "bin" / "verify-export.sh"), str(human), "--archive", str(human_zip), "--strict"],
                cwd=repo, env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(human_verified.returncode, 0, human_verified.stdout + "\n" + human_verified.stderr)
            self.assertIn("RESULT OK", human_verified.stdout)
            self.assertIn("COVERAGE missing=", human_verified.stdout)
            self.assertIn("missing=0 is not full platform availability", human_verified.stdout)


class MemoryStrictTests(unittest.TestCase):
    def test_default_max_age_is_two_days(self):
        self.assertEqual(verify.DEFAULT_MEMORY_MAX_AGE_DAYS, 2)

    def _export(self, root: Path, age_days: float | None):
        agent_data = root / "agent-data"
        agent_data.mkdir(parents=True)
        conn = sqlite3.connect(agent_data / "search-index.db")
        conn.execute("CREATE TABLE messages (agent_id TEXT, entry_id TEXT, timestamp_ms INTEGER)")
        conn.commit()
        conn.close()
        export = root / "virtual-office"
        convo = export / "01-assistants" / "Test assistant" / "conversations"
        convo.mkdir(parents=True)
        cutoff = 1792600000
        stats = {
            "cutoff_ms": cutoff * 1000,
            "cutoff_taipei": "2026-10-22 00:26:40",
            "agents": [{
                "agent_id": AGENT_ID, "name": "Test assistant", "kind": "agent",
                "conv_dir": str(convo.relative_to(export)),
                "messages": 0, "first": None, "last": None,
            }],
        }
        prog = export / "99-backup-program"
        (prog / "source" / "grok_bot_backup").mkdir(parents=True)
        (prog / "source" / "grok_bot_backup" / "common.py").write_text("# demo\n", encoding="utf-8")
        (prog / "COMMIT").write_text("a" * 40, encoding="utf-8")
        (prog / "conversation-stats.json").write_text(json.dumps(stats), encoding="utf-8")
        (export / "memory-index.md").write_text("# Memory index\n\nnot exported\n", encoding="utf-8")
        if age_days is not None:
            mem = convo.parent / "memory"
            mem.mkdir()
            file = mem / "memory-agent.jsonl"
            file.write_text(json.dumps({
                "date": "2026-10-22", "tier": "note", "scope": "agent", "fact": "example fact",
            }) + "\n", encoding="utf-8")
            timestamp = cutoff - age_days * 86400
            os.utime(file, (timestamp, timestamp))
        return export, agent_data

    def _run(self, export: Path, agent_data: Path, strict: bool, env: dict):
        args = ["verify", str(export)] + (["--strict"] if strict else [])
        buf = io.StringIO()
        with patch.object(sys, "argv", args), \
             patch.object(verify, "SEARCH_INDEX", agent_data / "search-index.db"), \
             patch.object(verify, "AGENT_DATA", agent_data), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("MEMORY_MAX_AGE_DAYS", None)
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as outcome:
                    verify.main()
        return outcome.exception.code, buf.getvalue()

    def test_missing_memory_fails_strict_and_recent_memory_passes_with_default_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_export, data = self._export(root / "missing", None)
            code, output = self._run(missing_export, data, True, {
                "MEMORY_DROP": str(root / "no-drop"),
            })
            self.assertEqual(code, 1)
            self.assertIn("FAIL", output)
            self.assertIn("no platform memory export found", output)
            fresh_root = root / "fresh"
            fresh_export, fresh_data = self._export(fresh_root, 1)
            code, output = self._run(fresh_export, fresh_data, True, {
                "MEMORY_DROP": str(root / "no-drop"),
            })
            self.assertEqual(code, 0, output)
            self.assertIn("RESULT OK", output)
            stale_export, stale_data = self._export(root / "stale", 5)
            code, output = self._run(stale_export, stale_data, True, {
                "MEMORY_DROP": str(root / "no-drop"),
            })
            self.assertEqual(code, 1)
            self.assertIn("FAIL", output)
            self.assertIn("older than 2 days", output)

    def test_coverage_missing_zero_is_not_full_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cells = {name: "unavailable" for name in coverage.DIMENSIONS}
            coverage.write_reports(root, [coverage._pack_row(AGENT_ID, "Test assistant", cells, {})])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertTrue(coverage.report_coverage(root, strict=True, show=True))
            output = buf.getvalue()
            self.assertIn("COVERAGE missing=0", output)
            self.assertIn("missing=0 is not full platform availability", output)
            self.assertIn("missing=0 is not full platform availability", (root / "coverage.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
