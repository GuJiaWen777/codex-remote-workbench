# Security and privacy

## Configuration

Keep private configuration outside the repository. Never commit SSH keys, SSH configuration, host aliases, actual addresses, usernames, project paths, backups or runtime logs. A private GitHub repository is not a credential store.

The example config is synthetic. The tool itself stores no SSH password: authentication is delegated to system OpenSSH. Host verification is `StrictHostKeyChecking=yes`; unknown fingerprints must be verified outside this tool.

## Trust model

Trust the local config, OpenSSH executable, remote account, Python runtime and Codex executable. The worker validates intended file operations and stages parser writes, but does not isolate hostile software or users with access to the same account/workspace.

Root locks only coordinate this tool. Hash checks catch many accidental concurrent changes, not all filesystem races. Avoid concurrent edits. Multi-file commits are not atomic and cannot guarantee recovery after process termination, host restart or disk failure.

Backups and audit records contain private remote data and remain on the remote host. Restrict their access and plan disk retention. Do not attach them to public issues. Tool results may also contain file content, hashes and paths visible in your local AI session.

## Reporting

For a vulnerability, contact the repository owner privately through an available GitHub contact channel. Do not post credentials, real server details or exploit output in public issues.
