"""Automatic, foreground-only launcher for the portable Windows recovery edition."""
import argparse
import os
from pathlib import Path
import sys
import time
import project_recovery as recovery


def wait_until_closed(timeout_seconds=86400):
    deadline = time.monotonic() + timeout_seconds
    announced = False
    while True:
        try:
            recovery.require_closed()
            if announced:
                print("\nCodex is closed. Starting recovery...", flush=True)
                time.sleep(2)
            return
        except recovery.RecoveryError as error:
            if not str(error).startswith("Fully quit"):
                raise
            if not announced:
                print("\nPLEASE CLOSE CODEX / CHATGPT NOW", flush=True)
                print("Use Quit/Exit, including the tray icon if present.", flush=True)
                print("Also close any Codex CLI sessions you have open.", flush=True)
                print("Keep THIS recovery window open. It will continue automatically.", flush=True)
                print("Your app will not be force-closed. Press Ctrl+C to cancel.\n", flush=True)
                announced = True
            if time.monotonic() >= deadline:
                raise recovery.RecoveryError("Timed out waiting for Codex to close. Run START-RECOVERY.bat again.")
            time.sleep(1)


def run(home, data, diagnose=False):
    print("CODEX PROJECT RECOVERY - PORTABLE WINDOWS EDITION", flush=True)
    print("Restores local project grouping. Creates backups before repairs.", flush=True)
    print("Does not rewrite conversation text or install a background service.\n", flush=True)
    if not diagnose:
        wait_until_closed()
    print("Checking saved projects and memberships...", flush=True)
    plan = recovery.build_plan(home, data, match_folders=True)
    recovery.show(plan["report"])
    report_file = data / "reports" / ("diagnosis-" + recovery.stamp() + ".json")
    recovery.atomic_json(report_file, plan["report"])
    if diagnose:
        print("\nDiagnosis finished. No Codex records were changed.", flush=True)
        return 0
    if not plan["report"]["organization_changes"] and not plan["updates"]:
        snapshot = recovery.snapshot(home, data)
        print("\nPROJECT RECORDS ALREADY AGREE - NO REPAIR NEEDED", flush=True)
        print("Saved an organization backup: " + str(snapshot), flush=True)
        print("You can reopen Codex now.", flush=True)
        return 0
    print("\nCreating rollback backups and restoring missing organization...", flush=True)
    folder = recovery.apply_plan(plan, home, data)
    print("\nSUCCESS - RECOVERY COMPLETE", flush=True)
    print("You can reopen Codex / ChatGPT now.", flush=True)
    print("Rollback backups: " + str(folder), flush=True)
    print("This program has finished. It does not keep running in the background.", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexProjectRecovery")
    args = parser.parse_args()
    try:
        return run(args.codex_home.resolve(), args.data_dir.resolve(), args.diagnose)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. You can close this window.", file=sys.stderr, flush=True)
        return 1
    except Exception as error:
        print("\nRECOVERY STOPPED", file=sys.stderr, flush=True)
        print(str(error), file=sys.stderr, flush=True)
        print("Keep any backup folders. Read START-HERE.txt for help.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
