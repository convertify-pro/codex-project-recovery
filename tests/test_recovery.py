"""Synthetic fixtures only. No real account, project, or chat data."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import project_recovery as recovery


@contextmanager
def database(path):
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "profile" / ".codex"
        self.home.mkdir(parents=True)
        self.data = self.base / "tool-data"
        self.host = "local:" + str(self.home)
        self.state_path = self.home / ".codex-global-state.json"
        self.state = {
            "local-projects": {"legacy-b": {"id": "legacy-b", "name": "Birch renamed",
                                             "rootPaths": ["X:\\Birch"], "createdAt": 2, "updatedAt": 3}},
            "thread-project-assignments": {"new": {"projectKind": "local", "projectId": "legacy-b"}},
            "app-server-project-id-by-legacy-project-id-by-host": {self.host: {"legacy-b": "server-b"}},
            "projectless-thread-ids": ["old", "standalone"],
            "project-order": ["legacy-b"],
            "electron-persisted-atom-state": {"unrelated-preference": {"keep": True}},
            "unrelated-account-state": {"private_sentinel": "must-stay-local"},
        }
        recovery.atomic_json(self.state_path, self.state)
        old = {
            "local-projects": {"legacy-a": {"id": "legacy-a", "name": "Atlas", "rootPaths": ["X:\\Atlas"]}},
            "thread-project-assignments": {
                "old": {"projectKind": "local", "projectId": "legacy-a"},
                "new": {"projectKind": "local", "projectId": "legacy-a"},
                "deleted-thread": {"projectKind": "local", "projectId": "legacy-a"},
            },
            "app-server-project-id-by-legacy-project-id-by-host": {self.host: {"legacy-a": "server-a"}},
        }
        recovery.atomic_json(self.home / "..codex-global-state.json.tmp-older", old)
        self.db = self.home / "state_5.sqlite"
        with database(self.db) as con:
            con.executescript('''
                CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,position INTEGER,
                    created_at_ms INTEGER,updated_at_ms INTEGER);
                CREATE TABLE project_roots(project_id TEXT REFERENCES projects(id),position INTEGER,path TEXT);
                CREATE TABLE threads(id TEXT PRIMARY KEY,cwd TEXT,source TEXT,created_at_ms INTEGER,
                    project_id TEXT REFERENCES projects(id),body TEXT);
            ''')
            con.executemany("INSERT INTO projects VALUES(?,?,?,?,?)", [
                ("server-a", "Atlas", 0, 1, 1), ("server-b", "Birch", 1, 2, 2),
                ("server-c", "Cedar", 2, 3, 3)])
            con.executemany("INSERT INTO project_roots VALUES(?,?,?)", [
                ("server-a", 0, "X:\\Atlas"), ("server-b", 0, "X:\\Birch"),
                ("server-c", 0, "X:\\Cedar")])
            con.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?)", [
                ("old", "X:\\Atlas", "vscode", 10, None, "old-message-sentinel"),
                ("new", "X:\\Birch", "vscode", 11, None, "new-message-sentinel"),
                ("folder", "\\\\?\\X:\\Cedar", "vscode", 12, None, "folder-message"),
                ("backend", "X:\\Cedar", "vscode", 13, "server-c", "backend-message"),
                ("standalone", "X:\\Cedar", "vscode", 14, None, "standalone-message"),
                ("agent", "X:\\Cedar", '{"subagent":{}}', 15, None, "agent-message")])

    def tearDown(self):
        self.temp.cleanup()

    def test_diagnosis_does_not_change_source_files(self):
        original_state, original_db = self.state_path.read_bytes(), self.db.read_bytes()
        plan = recovery.build_plan(self.home, self.data)
        self.assertEqual(len(plan["report"]["projects_to_restore"]), 2)
        self.assertEqual(plan["result"]["thread-project-assignments"]["new"]["projectId"], "legacy-b")
        self.assertNotIn("deleted-thread", plan["result"]["thread-project-assignments"])
        self.assertEqual(self.state_path.read_bytes(), original_state)
        self.assertEqual(self.db.read_bytes(), original_db)

    def test_apply_preserves_chats_new_assignments_and_unrelated_state(self):
        with database(self.db) as con:
            bodies_before = con.execute("SELECT id,body FROM threads ORDER BY id").fetchall()
        plan = recovery.build_plan(self.home, self.data)
        folder = recovery.apply_plan(plan, self.home, self.data, check_closed=False)
        result = recovery.read_json(self.state_path)
        self.assertEqual(result["unrelated-account-state"], self.state["unrelated-account-state"])
        self.assertEqual(result["local-projects"]["legacy-b"], self.state["local-projects"]["legacy-b"])
        self.assertEqual(result["electron-persisted-atom-state"]["unrelated-preference"], {"keep": True})
        with database(self.db) as con:
            self.assertEqual(con.execute("SELECT id,body FROM threads ORDER BY id").fetchall(), bodies_before)
            self.assertEqual(con.execute("SELECT project_id FROM threads WHERE id='new'").fetchone()[0], "server-b")
            self.assertEqual(con.execute("SELECT project_id FROM threads WHERE id='old'").fetchone()[0], "server-a")
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertTrue((folder / self.db.name).exists())
        self.assertTrue((folder / "undo.json").exists())
        second = recovery.build_plan(self.home, self.data)
        self.assertFalse(second["report"]["organization_changes"])
        self.assertEqual(second["updates"], [])

    def test_matching_is_opt_in_skips_agents_and_explicit_projectless(self):
        normal = recovery.build_plan(self.home, self.data)
        self.assertNotIn("folder", normal["result"]["thread-project-assignments"])
        matching = recovery.build_plan(self.home, self.data, True)
        assignments = matching["result"]["thread-project-assignments"]
        self.assertIn("folder", assignments)
        self.assertNotIn("agent", assignments)
        self.assertNotIn("standalone", assignments)

    def test_ambiguous_matching_skipped(self):
        with database(self.db) as con:
            con.execute("INSERT INTO project_roots VALUES('server-b',1,'X:\\Cedar')")
        plan = recovery.build_plan(self.home, self.data, True)
        self.assertNotIn("folder", plan["result"]["thread-project-assignments"])
        self.assertEqual(plan["report"]["ambiguous_folders_skipped"], 1)

    def test_backend_conflicts_preserved(self):
        with database(self.db) as con:
            con.execute("UPDATE threads SET project_id='server-a' WHERE id='new'")
        plan = recovery.build_plan(self.home, self.data)
        self.assertEqual(plan["report"]["existing_membership_conflicts_preserved"], 1)
        self.assertNotIn(("server-b", "new"), plan["updates"])

    def test_invalid_sources_ignored_and_unknown_schema_stops(self):
        (self.home / "..codex-global-state.json.tmp-broken").write_text("{broken", encoding="utf-8")
        self.assertEqual(recovery.build_plan(self.home, self.data)["report"]["valid_saved_sources"], 1)
        with database(self.home / "state_6.sqlite") as con:
            con.execute("CREATE TABLE unrelated(x)")
        with self.assertRaises(recovery.RecoveryError):
            recovery.build_plan(self.home, self.data)

    def test_changed_state_aborts_before_writes(self):
        plan = recovery.build_plan(self.home, self.data)
        state = copy.deepcopy(self.state)
        state["a-new-user-setting"] = True
        recovery.atomic_json(self.state_path, state)
        with self.assertRaises(recovery.RecoveryError):
            recovery.apply_plan(plan, self.home, self.data, check_closed=False)

    def test_changed_database_aborts_and_rolls_back(self):
        plan = recovery.build_plan(self.home, self.data)
        with database(self.db) as con:
            con.execute("UPDATE threads SET project_id='server-c' WHERE id='old'")
        with self.assertRaises(recovery.RecoveryError):
            recovery.apply_plan(plan, self.home, self.data, check_closed=False)
        self.assertEqual(recovery.read_json(self.state_path), self.state)
        with database(self.db) as con:
            self.assertEqual(con.execute("SELECT project_id FROM threads WHERE id='old'").fetchone()[0], "server-c")
            self.assertIsNone(con.execute("SELECT project_id FROM threads WHERE id='new'").fetchone()[0])

    def test_snapshot_excludes_chat_bodies_and_account_fields(self):
        path = recovery.snapshot(self.home, self.data, check_closed=False)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("message-sentinel", text)
        self.assertNotIn("private_sentinel", text)
        self.assertNotIn("electron-persisted-atom-state", text)
        self.assertIn("Atlas", text)

    def test_undo_preserves_new_message_text(self):
        plan = recovery.build_plan(self.home, self.data)
        folder = recovery.apply_plan(plan, self.home, self.data, check_closed=False)
        with database(self.db) as con:
            con.execute("UPDATE threads SET body='a newer conversation message' WHERE id='new'")
        recovery.undo_recovery(folder, self.home, self.data, check_closed=False)
        self.assertEqual(recovery.read_json(self.state_path), self.state)
        with database(self.db) as con:
            self.assertEqual(con.execute("SELECT body FROM threads WHERE id='new'").fetchone()[0], "a newer conversation message")
            self.assertIsNone(con.execute("SELECT project_id FROM threads WHERE id='old'").fetchone()[0])

    def test_undo_refuses_newer_desktop_state(self):
        plan = recovery.build_plan(self.home, self.data)
        folder = recovery.apply_plan(plan, self.home, self.data, check_closed=False)
        state = recovery.read_json(self.state_path)
        state["new-user-setting"] = "preserve"
        recovery.atomic_json(self.state_path, state)
        with self.assertRaises(recovery.RecoveryError):
            recovery.undo_recovery(folder, self.home, self.data, check_closed=False)

    def test_open_app_stops_mutation(self):
        plan = recovery.build_plan(self.home, self.data)
        with patch.object(recovery, "require_closed", side_effect=recovery.RecoveryError("App open")):
            with self.assertRaises(recovery.RecoveryError):
                recovery.apply_plan(plan, self.home, self.data)
        self.assertEqual(recovery.read_json(self.state_path), self.state)

    def test_state_write_failure_rolls_back_database(self):
        plan = recovery.build_plan(self.home, self.data)
        original_writer = recovery.atomic_json
        def fail_saved_state(path, value):
            if path == self.state_path:
                raise OSError("simulated disk write failure")
            return original_writer(path, value)
        with patch.object(recovery, "atomic_json", side_effect=fail_saved_state):
            with self.assertRaises(recovery.RecoveryError):
                recovery.apply_plan(plan, self.home, self.data, check_closed=False)
        self.assertEqual(recovery.read_json(self.state_path), self.state)
        with database(self.db) as con:
            self.assertIsNone(con.execute("SELECT project_id FROM threads WHERE id='old'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
