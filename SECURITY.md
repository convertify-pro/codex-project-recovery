# Security and privacy

The release runs locally and offline. It reads Codex project state and its local SQLite database to diagnose and repair project organization. It does not read authentication files, make network requests, or transmit diagnostics.

The repair creates backups under `%LOCALAPPDATA%\CodexProjectRecovery`. Full rollback backups may contain conversation data and local project paths. Keep these files private. Never include them in an issue or pull request.

For a suspected security issue, open a private GitHub security advisory if that option is available for the repository. Otherwise, report the concern without attaching user records or credentials.
