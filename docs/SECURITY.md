# Security and authority boundaries

## Defaults

- Bind to loopback only.
- Require an access token for any non-loopback bind.
- Allow only configured project roots.
- Resolve every tool path and reject project-root escape.
- Treat `.env`, credential folders and profile-sealed paths as unreadable.
- Auto-run read-only tools only.
- Pause file mutation and shell commands for one-time approval.
- Block destructive command patterns and broker/trading actions.
- Keep secrets in process environment; never return them through `/api/config`.
- Store mission history locally in SQLite.

## Known limits

- Pattern-based shell blocking cannot prove an arbitrary command safe. Run the Harness as a non-admin account and use Docker/VM isolation for untrusted repositories.
- Project files can contain prompt injection. Model instructions do not grant permissions; policy still applies, but data exfiltration through an approved network command remains possible.
- The access token protects the API but is not a complete internet-facing authentication system. Use Tailnet access, not public port forwarding.
- SQLite event hashes detect accidental or direct tampering; they are not externally anchored signatures.
- Model providers receive whatever context the router sends. Enable local-only for sensitive data and inspect route events.

## Incident response

1. Cancel the mission.
2. Stop the server.
3. Revoke affected provider keys.
4. Preserve `data/harness.db` and repository diff for evidence.
5. Inspect the mission event stream and approvals.
6. Revert through Git or a known-good backup.
7. Add a regression test or policy rule before re-enabling the affected action.

