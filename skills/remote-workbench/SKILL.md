---
name: remote-workbench
description: Use the remote_workbench MCP to inspect and safely edit configured remote server projects, including bounded directory listing and literal text search.
---

# Remote Workbench

Resolve the user's SSH alias and project to the configured MCP host key and allowed absolute workdir. Read the local private configuration only as needed; never expose credentials. If several projects match and context does not resolve the target, ask. A local checkout is not necessarily the current remote source.

Use the currently available MCP tool schemas. Run `remote_doctor` for a new connection context; reuse a recent successful check if unchanged. Read applicable remote `AGENTS.md` and relevant project documents. Use `remote_list` and `remote_search` for discovery when available, observing their exclusions, skipped-file count and truncation flags. Search is literal, not regex. A connection check does not prove patch compatibility or tests passed.

For edits, `remote_inspect` every target immediately before editing. Prepare a standard Codex patch with exactly those inspected SHA-256 hashes in `expectedHashes`; use null only for a confirmed missing new file. Run `remote_apply_patch` with `dryRun: true`, review the preview, then apply with the same patch and hashes when the user's task authorizes the change. Reinspect changed files and run appropriate tests through an available authorized means. Preview creates remote staging and audit artifacts.

If a hash conflicts, reinspect and reconstruct the patch. If SSH times out or disconnects during a write, inspect the targets and available audit status before any retry; the outcome may be partial. The tool is not a crash-safe multi-file transaction or an OS sandbox. Backups and audits can contain sensitive data.

The MCP does not run arbitrary commands or manage services. Do not bypass denied workspaces, broaden configuration, or install software merely because a task uses this skill. Report any unavailable verification and the audit/backup path after writes.
