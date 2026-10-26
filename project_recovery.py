"""Windows local project organization recovery. Standard-library-only, offline, manual."""
import argparse
import copy
import csv
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone

VERSION = "1.1.0"
ORG_KEYS = (
    "local-projects", "thread-project-assignments", "thread-project-membership-host-ids",
    "app-server-project-id-by-legacy-project-id-by-host", "projectless-thread-ids",
    "project-order", "sidebar-project-thread-orders", "project-appearances",
)


class RecoveryError(Exception):
    pass


def read_json(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RecoveryError("Expected a JSON object in " + str(path))
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".recovery-tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def normalize(path):
    return ntpath.normcase(ntpath.normpath(re.sub(r"^\\+\?\\+", "", path)))


def require_closed():
    if os.name != "nt":
        raise RecoveryError("This release supports Windows only.")
    result = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True,
                            text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    # Case-insensitive matching also catches the codex.exe local server/CLI.
    running = {row[0].lower() for row in csv.reader(io.StringIO(result.stdout)) if row}
    if running & {"chatgpt.exe", "codex.exe"}:
        raise RecoveryError("Fully quit ChatGPT/Codex and any Codex CLI sessions, then run this again.")


def database_path(home):
    candidates = [(int(match.group(1)), path) for path in home.glob("state_*.sqlite")
                  if (match := re.fullmatch(r"state_(\d+)\.sqlite", path.name))]
    if not candidates:
        raise RecoveryError("No local state database found in " + str(home))
    return max(candidates)[1]


def inspect(home):
    state_path = home / ".codex-global-state.json"
    if not state_path.is_file():
        raise RecoveryError("No saved desktop state found in " + str(home))
    current = read_json(state_path)
    for key in ORG_KEYS:
        expected = list if key in {"projectless-thread-ids", "project-order"} else dict
        if key in current and not isinstance(current[key], expected):
            raise RecoveryError("Unsupported desktop state shape: " + key)
    if not isinstance(current.get("electron-persisted-atom-state", {}), dict):
        raise RecoveryError("Unsupported sidebar preference shape.")
    db = database_path(home)
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        required = {
            "projects": {"id", "name", "position", "created_at_ms", "updated_at_ms"},
            "project_roots": {"project_id", "position", "path"},
            "threads": {"id", "cwd", "source", "created_at_ms", "project_id"},
        }
        for table, columns in required.items():
            actual = {row["name"] for row in con.execute("PRAGMA table_info(" + table + ")")}
            if not columns <= actual:
                raise RecoveryError("Unsupported database layout in " + db.name + ": " + table)
        projects = [dict(row) for row in con.execute(
            "SELECT id,name,position,created_at_ms,updated_at_ms FROM projects ORDER BY position,id")]
        roots = {}
        for row in con.execute("SELECT project_id,path FROM project_roots ORDER BY project_id,position"):
            roots.setdefault(row["project_id"], []).append(row["path"])
        threads = {row["id"]: dict(row) for row in con.execute(
            "SELECT id,cwd,source,created_at_ms,project_id FROM threads")}
    finally:
        con.close()
    return current, state_path, db, projects, roots, threads


def sources(home, data):
    paths = list(home.glob("*codex-global-state*.json*"))
    paths += list((data / "snapshots").glob("*.json"))
    loaded = []
    for path in paths:
        if path.name == ".codex-global-state.json" or not path.is_file():
            continue
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                continue
            value = read_json(path)
            if not isinstance(value.get("local-projects"), dict):
                continue
            if not isinstance(value.get("thread-project-assignments", {}), dict):
                continue
            if not isinstance(value.get("app-server-project-id-by-legacy-project-id-by-host", {}), dict):
                continue
            # Tool snapshots belong to one resolved Codex home, never another user's profile.
            if value.get("recovery_snapshot_home") and value["recovery_snapshot_home"] != str(home):
                continue
            loaded.append((path.stat().st_mtime, path, value))
        except (OSError, ValueError, RecoveryError):
            continue
    return sorted(loaded, key=lambda entry: (entry[0], str(entry[1])))


def build_plan(home, data, match_folders=False):
    current, state_path, db, projects, roots, threads = inspect(home)
    candidates = sources(home, data)
    host = "local:" + str(home)
    canonical = {p["id"] for p in projects}
    old_assignments, legacy_map = {}, {}
    for _, _, value in candidates:
        legacy_map.update(value.get("app-server-project-id-by-legacy-project-id-by-host", {}).get(host, {}))
        old_assignments.update(value.get("thread-project-assignments", {}))
    legacy_map.update(current.get("app-server-project-id-by-legacy-project-id-by-host", {}).get(host, {}))
    reverse = {server: legacy for legacy, server in legacy_map.items() if server in canonical}
    for legacy, server in current.get("app-server-project-id-by-legacy-project-id-by-host", {}).get(host, {}).items():
        if server in canonical:
            reverse[server] = legacy
    result = copy.deepcopy(current)
    local = result.setdefault("local-projects", {})
    mappings = result.setdefault("app-server-project-id-by-legacy-project-id-by-host", {}).setdefault(host, {})
    added = []
    for p in projects:
        pid = reverse.setdefault(p["id"], p["id"])
        mappings[pid] = p["id"]
        if pid not in local:
            local[pid] = {"id": pid, "name": p["name"], "rootPaths": roots.get(p["id"], []),
                          "createdAt": p["created_at_ms"], "updatedAt": p["updated_at_ms"]}
            added.append({"id": pid, "name": p["name"]})
    memberships = result.setdefault("thread-project-assignments", {})
    existing_memberships = copy.deepcopy(memberships)
    backend_added = saved_added = folder_added = 0
    conflicts = []
    for tid, thread in threads.items():
        backend = thread["project_id"]
        if backend in reverse:
            if tid not in memberships:
                memberships[tid] = {"projectKind": "local", "projectId": reverse[backend]}
                backend_added += 1
            elif mappings.get(memberships[tid].get("projectId")) != backend:
                conflicts.append(tid)
    for tid, assignment in old_assignments.items():
        if not isinstance(assignment, dict) or assignment.get("projectKind") != "local":
            continue
        old_pid = assignment.get("projectId")
        server = legacy_map.get(old_pid, old_pid)
        if tid in threads and tid not in memberships and server in reverse:
            memberships[tid] = {"projectKind": "local", "projectId": reverse[server]}
            saved_added += 1
    path_projects = {}
    for server, paths in roots.items():
        for path in paths:
            path_projects.setdefault(normalize(path), set()).add(reverse[server])
    ambiguous = 0
    if match_folders:
        excluded = set(current.get("projectless-thread-ids", []))
        for tid, thread in threads.items():
            if tid in memberships or tid in excluded or thread["source"] not in {"vscode", "cli"}:
                continue
            choices = path_projects.get(normalize(thread["cwd"]), set())
            if len(choices) == 1:
                memberships[tid] = {"projectKind": "local", "projectId": next(iter(choices))}
                folder_added += 1
            elif len(choices) > 1:
                ambiguous += 1
    hosts = result.setdefault("thread-project-membership-host-ids", {})
    updates = []
    for tid, assignment in memberships.items():
        if not isinstance(assignment, dict) or assignment.get("projectKind") != "local":
            continue
        server = mappings.get(assignment.get("projectId"))
        if tid in threads and server in canonical:
            hosts[tid] = "local"
            if threads[tid]["project_id"] is None:
                updates.append((server, tid))
    result["projectless-thread-ids"] = [tid for tid in current.get("projectless-thread-ids", [])
                                        if tid not in memberships]
    order = list(dict.fromkeys(current.get("project-order", []) + [reverse[p["id"]] for p in projects]))
    result["project-order"] = order
    atoms = result.setdefault("electron-persisted-atom-state", {})
    unified = atoms.get("unified-sidebar-project-order-v1", [])
    if not isinstance(unified, list):
        raise RecoveryError("Unsupported sidebar order layout.")
    atoms["unified-sidebar-project-order-v1"] = list(dict.fromkeys(
        unified + ["codex:project:" + pid for pid in order]))
    preferences = atoms.setdefault("flat-project-sidebar-preferences-v1", {})
    if not isinstance(preferences, dict):
        raise RecoveryError("Unsupported sidebar grouping layout.")
    preferences.update({"mode": "project", "initialized": True})
    migration = result.get("app-server-projects-migration-by-host", {}).get(host)
    if isinstance(migration, dict) and isinstance(migration.get("pendingThreadAssignmentIds"), list):
        resolved = {tid for _, tid in updates} | {tid for tid, t in threads.items() if t["project_id"] in canonical}
        migration["pendingThreadAssignmentIds"] = [tid for tid in migration["pendingThreadAssignmentIds"]
                                                   if tid not in resolved or tid in conflicts]
        migration["threadAssignmentsMigrated"] = not migration["pendingThreadAssignmentIds"]
    for pid, value in current.get("local-projects", {}).items():
        if local[pid] != value:
            raise RecoveryError("Existing project preservation check failed.")
    for tid, value in existing_memberships.items():
        if memberships[tid] != value:
            raise RecoveryError("Existing membership preservation check failed.")
    report = {
        "tool_version": VERSION, "codex_home": str(home), "database": db.name,
        "desktop_projects": len(current.get("local-projects", {})), "database_projects": len(projects),
        "projects_to_restore": added, "memberships_from_database": backend_added,
        "memberships_from_saved_records": saved_added, "memberships_from_folder_matches": folder_added,
        "database_memberships_to_fill": len(updates), "ambiguous_folders_skipped": ambiguous,
        "existing_membership_conflicts_preserved": len(conflicts), "valid_saved_sources": len(candidates),
        "organization_changes": result != current,
    }
    return {"current": current, "result": result, "state_path": state_path, "db": db,
            "updates": updates, "report": report, "original_state_hash": digest(state_path)}


def show(report):
    print("\nCodex Project Recovery " + VERSION)
    print("Saved sidebar projects: {} | Database projects: {}".format(
        report["desktop_projects"], report["database_projects"]))
    print("Projects to restore: " + str(len(report["projects_to_restore"])))
    for project in report["projects_to_restore"]:
        print("  + " + project["name"])
    print("Memberships from database / saved records / folder matches: {} / {} / {}".format(
        report["memberships_from_database"], report["memberships_from_saved_records"],
        report["memberships_from_folder_matches"]))
    print("Existing database memberships to fill: " + str(report["database_memberships_to_fill"]))
    print("Conflicting existing assignments preserved: " + str(report["existing_membership_conflicts_preserved"]))
    print("Valid saved sources: " + str(report["valid_saved_sources"]))


def snapshot(home, data, check_closed=True):
    if check_closed:
        require_closed()
    current, _, _, projects, roots, threads = inspect(home)
    value = {key: copy.deepcopy(current[key]) for key in ORG_KEYS if key in current}
    value.update({"recovery_snapshot_home": str(home), "recovery_snapshot_version": VERSION,
                  "saved_at": datetime.now(timezone.utc).isoformat()})
    host = "local:" + str(home)
    mapping = value.setdefault("app-server-project-id-by-legacy-project-id-by-host", {}).setdefault(host, {})
    reverse = {server: legacy for legacy, server in mapping.items()}
    local = value.setdefault("local-projects", {})
    for p in projects:
        pid = reverse.setdefault(p["id"], p["id"])
        mapping[pid] = p["id"]
        local.setdefault(pid, {"id": pid, "name": p["name"], "rootPaths": roots.get(p["id"], []),
                               "createdAt": p["created_at_ms"], "updatedAt": p["updated_at_ms"]})
    assignments = value.setdefault("thread-project-assignments", {})
    for tid, thread in threads.items():
        if thread["project_id"] in reverse:
            assignments.setdefault(tid, {"projectKind": "local", "projectId": reverse[thread["project_id"]]})
    target = data / "snapshots" / ("organization-" + stamp() + ".json")
    atomic_json(target, value)
    return target


def apply_plan(plan, home, data, check_closed=True):
    if check_closed:
        require_closed()
    if digest(plan["state_path"]) != plan["original_state_hash"]:
        raise RecoveryError("Saved state changed after diagnosis. Run diagnosis again.")
    backup_dir = data / "backups" / stamp()
    backup_dir.mkdir(parents=True)
    shutil.copy2(plan["state_path"], backup_dir / plan["state_path"].name)
    state_bak = plan["state_path"].with_suffix(".json.bak")
    if state_bak.exists():
        shutil.copy2(state_bak, backup_dir / state_bak.name)
    # Capture a consistent database backup with its WAL included through SQLite's backup API.
    source = sqlite3.connect(plan["db"], timeout=10)
    try:
        with closing(sqlite3.connect(backup_dir / plan["db"].name)) as destination:
            source.backup(destination)
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RecoveryError("Database integrity check failed; no recovery applied.")
        if check_closed:
            require_closed()
        if digest(plan["state_path"]) != plan["original_state_hash"]:
            raise RecoveryError("Saved state changed during backup; no recovery applied.")
        source.execute("PRAGMA foreign_keys=ON")
        if source.execute("PRAGMA foreign_key_check").fetchall():
            raise RecoveryError("Existing database foreign-key errors; no recovery applied.")
        changed = []
        with source:
            for server, tid in plan["updates"]:
                # Abort rather than guessing if another process changed a membership since diagnosis.
                row = source.execute("SELECT project_id FROM threads WHERE id=?", (tid,)).fetchone()
                if row is None or row[0] is not None:
                    raise RecoveryError("Database memberships changed after diagnosis. Run diagnosis again.")
                source.execute("UPDATE threads SET project_id=? WHERE id=? AND project_id IS NULL", (server, tid))
                changed.append({"thread_id": tid, "before": None, "after": server})
            if source.execute("PRAGMA foreign_key_check").fetchall():
                raise RecoveryError("Foreign-key verification failed; database changes rolled back.")
            # A precise organization-only undo journal avoids reverting newer chat contents.
            atomic_json(backup_dir / "undo.json", {"codex_home": str(home), "database": plan["db"].name,
                                                   "memberships": changed})
            atomic_json(plan["state_path"], plan["result"])
            atomic_json(state_bak, plan["result"])
            if read_json(plan["state_path"]) != plan["result"]:
                raise RecoveryError("Saved-state verification failed. Backups are in " + str(backup_dir))
            undo = read_json(backup_dir / "undo.json")
            undo["state_after_sha256"] = digest(plan["state_path"])
            atomic_json(backup_dir / "undo.json", undo)
    except Exception as exc:
        # If a later file write or transaction commit failed, restore the saved state
        # only when it is still our own output. Never replace concurrent user edits.
        try:
            if read_json(plan["state_path"]) == plan["result"]:
                atomic_json(plan["state_path"], plan["current"])
            if state_bak.exists() and read_json(state_bak) == plan["result"]:
                before_bak = backup_dir / state_bak.name
                atomic_json(state_bak, read_json(before_bak) if before_bak.exists() else plan["current"])
        except (OSError, ValueError, RecoveryError):
            pass  # Complete backups and the undo journal remain available for manual recovery.
        raise RecoveryError(str(exc) + "\nRecovery backup directory: " + str(backup_dir)) from exc
    finally:
        source.close()
    atomic_json(backup_dir / "result.json", {"status": "complete", **plan["report"]})
    snapshot(home, data, check_closed=False)
    return backup_dir


def undo_recovery(folder, home, data, check_closed=True):
    if check_closed:
        require_closed()
    journal = read_json(folder / "undo.json")
    if journal.get("codex_home") != str(home):
        raise RecoveryError("This backup belongs to another Codex home.")
    state_path = home / ".codex-global-state.json"
    if digest(state_path) != journal.get("state_after_sha256"):
        raise RecoveryError("Desktop state has changed since this repair. Automatic undo stopped to preserve newer changes.")
    db_name = journal.get("database", "")
    if not re.fullmatch(r"state_\d+\.sqlite", db_name):
        raise RecoveryError("Invalid undo database name.")
    original = read_json(folder / state_path.name)
    pre_undo = data / "backups" / ("before-undo-" + stamp())
    pre_undo.mkdir(parents=True)
    shutil.copy2(state_path, pre_undo / state_path.name)
    with closing(sqlite3.connect(home / db_name, timeout=10)) as con:
        with closing(sqlite3.connect(pre_undo / db_name)) as backup:
            con.backup(backup)
        # Undo organization only; never restore the whole database over newer conversations.
        with con:
            for row in journal.get("memberships", []):
                con.execute("UPDATE threads SET project_id=? WHERE id=? AND project_id=?",
                            (row["before"], row["thread_id"], row["after"]))
    atomic_json(state_path, original)
    atomic_json(state_path.with_suffix(".json.bak"), original)
    return pre_undo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["diagnose", "repair", "snapshot", "undo"], nargs="?", default="diagnose")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                        / "CodexProjectRecovery")
    parser.add_argument("--match-folders", action="store_true", help="Opt in to unambiguous exact-folder matching")
    parser.add_argument("--backup", type=Path, help="Backup directory to undo, while app is closed")
    args = parser.parse_args(argv)
    home, data = args.codex_home.resolve(), args.data_dir.resolve()
    if args.mode == "undo":
        if not args.backup:
            raise RecoveryError("Undo requires --backup followed by the repair backup directory.")
        require_closed()
        if input("Type UNDO to undo organization changes from this repair: ").strip() != "UNDO":
            print("Cancelled.")
            return 0
        folder = undo_recovery(args.backup.resolve(), home, data)
        print("Organization changes undone. Before-undo backup: " + str(folder))
        return 0
    if args.mode == "snapshot":
        target = snapshot(home, data)
        print("Organization snapshot saved:\n" + str(target))
        return 0
    if args.mode == "repair":
        require_closed()
    plan = build_plan(home, data, args.match_folders)
    show(plan["report"])
    report_path = data / "reports" / ("diagnosis-" + stamp() + ".json")
    atomic_json(report_path, plan["report"])
    print("Report: " + str(report_path))
    if args.mode == "diagnose":
        print("Diagnosis finished. No Codex records were changed.")
        return 0
    if not plan["report"]["organization_changes"] and not plan["updates"]:
        print("No repair is needed.")
        return 0
    if not args.match_folders:
        answer = input("Also preview exact-folder matches for chats without saved memberships? [y/N] ").strip().lower()
        if answer == "y":
            plan = build_plan(home, data, True)
            show(plan["report"])
            atomic_json(report_path, plan["report"])
    print("Missing entries may include projects you intentionally removed. Review the list above.")
    if input("Type RESTORE to back up and apply this plan: ").strip() != "RESTORE":
        print("Cancelled. No Codex records were changed.")
        return 0
    folder = apply_plan(plan, home, data)
    print("\nRecovery complete. You can reopen ChatGPT/Codex.\nBackups: " + str(folder))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RecoveryError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        print("\nStopped: " + str(error), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        sys.exit(1)
