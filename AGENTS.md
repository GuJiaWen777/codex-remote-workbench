# Repository instructions

- Keep SSH aliases, IPs, usernames, private keys, real workspace paths, private configuration and runtime output outside Git.
- Examples must use unmistakable placeholders. Test fixtures must be synthetic.
- Never rewrite installed dependency packages. Keep OpenSSH verification enabled.
- The patch tool is not an OS sandbox or crash-safe transaction; preserve these boundaries in documentation.
- Run `npm test` and Python worker tests before changing remote-edit behavior.
- Real SSH tests require an explicitly supplied private config and a dedicated temporary test root.
- Do not silently install remote software, change global Codex/SSH settings, add arbitrary command execution, or publish this repository.
