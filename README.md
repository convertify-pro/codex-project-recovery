# Codex Project Recovery

Restore **local Codex projects that disappeared from the desktop sidebar while their chats still appear in Recents**.

> An unofficial community utility for a specific Windows symptom. It is not made or endorsed by OpenAI, and it cannot recover records that no longer exist in local saved data.

## Download

**[Download the one-click Windows ZIP](https://raw.githubusercontent.com/convertify-pro/codex-project-recovery/main/downloads/Codex-Project-Recovery-One-Click-Windows.zip)** · [SHA-256 checksum](https://raw.githubusercontent.com/convertify-pro/codex-project-recovery/main/downloads/Codex-Project-Recovery-One-Click-Windows.zip.sha256)

The ZIP includes a private portable runtime, so users do not install Python, packages, or other software. Source code, tests, licenses, checksums, and full usage instructions are included in this repository.

## Run it

1. Extract the entire ZIP to a folder.
2. Double-click `START-RECOVERY.bat`.
3. If Codex or ChatGPT is open, quit it when prompted. Leave the recovery window open; the tool waits and continues by itself.
4. When it says `SUCCESS - RECOVERY COMPLETE`, reopen Codex.

The tool never force-closes the app. It creates rollback backups before changing project records. It does not need administrator access. Press **Ctrl+C** to cancel while it waits.

## What it does

The tool checks the local Codex database against saved desktop project metadata. When records remain available, it restores missing local project entries and their chat memberships. It uses saved assignments first; it uses a working-folder match only when the folder points to exactly one project. It skips ambiguous folders, subagents, and chats explicitly marked projectless.

The utility changes local project organization and empty membership fields. It does not replace chat text, project files, account credentials, or file access settings. It makes no network requests and sends no telemetry. No background service or startup task is installed; it only runs when launched.

After repair, Codex writes current state normally. Run **Save Organization Backup** from an advanced/manual build after significant project changes if you want a recent recovery snapshot. This tool does not prevent future app bugs, and it should not be run as routine cleanup: old local records can include projects you intentionally removed.

## Requirements and limits

- Windows 10 or 11, 64-bit Intel/AMD.
- Local Codex desktop projects only. Cloud/ChatGPT projects, mobile clients, and remote or WSL hosts are outside the tool's scope.
- The repair depends on local project records still existing and on a supported database layout. It stops if it cannot verify the expected layout. Future Codex versions may change that format.
- The recovery approach was demonstrated on Windows Codex app build `26.1021.3705.0`; other app builds are not certified.

## Verify the release

The published checksum is `downloads/Codex-Project-Recovery-One-Click-Windows.zip.sha256`. In PowerShell, calculate the downloaded ZIP's SHA-256 and compare it with that file:

```powershell
Get-FileHash .\Codex-Project-Recovery-One-Click-Windows.zip -Algorithm SHA256
```

## Build from source

The interactive recovery source uses only Python's standard library. To build the one-click package, use a Windows x64 Python 3.14 installation and run `build_portable.py` from the repository root. The script creates the private runtime folder used by `START-RECOVERY.bat`.

Run the automated tests with:

```powershell
python -m unittest discover -s tests -v
```

The release's `TESTING.txt` records the validation performed for each package. The tests use synthetic fixtures; no real chats or project records are included.

## Privacy and backups

The utility stores diagnosis summaries, organization snapshots, and rollback backups under `%LOCALAPPDATA%\CodexProjectRecovery`. Compact organization snapshots include project names, local paths, IDs, and memberships. Full rollback backups can contain chat text and saved app state, so keep the local recovery folder private and share only the release ZIP.

## Report a problem

Please open a GitHub issue with your Windows version, Codex app build, and the exact error text. **Do not attach** `state_*.sqlite`, `.*codex-global-state*.json`, logs, or the recovery backups; these can contain private conversations and project paths.

## License

The recovery utility source is MIT licensed. The bundled CPython runtime and its components have separate notices in `PYTHON-LICENSE.txt` and `runtime/PYTHON-LICENSE.txt` in the release ZIP.
