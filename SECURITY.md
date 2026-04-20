# Security Policy

## Supported Versions

HydraHive is currently in active development. Security fixes are applied to the latest version on `main`.

| Version | Supported |
|---------|-----------|
| latest (main) | ✅ |
| older commits | ❌ |

## Reporting a Vulnerability

**Please do not report security vulnerabilities as public GitHub issues.**

Report security issues privately via GitHub's built-in security advisory system:
**[Report a vulnerability](../../security/advisories/new)**

Alternatively, contact the maintainer directly via Matrix or Discord (see profile).

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

### Response timeline

- Acknowledgement within **48 hours**
- Assessment and patch target within **7 days** for critical issues

## Threat Model

HydraHive is a **self-hosted agent server with code execution capabilities**. Understanding the threat model is required before deployment.

### What HydraHive can do

- Execute shell commands on the host system (via `shell_exec` tool)
- Read and write files in configured agent directories
- Make outbound HTTP requests to external APIs
- Spawn subprocesses (via `run_process` tool)
- Access credentials stored in `/etc/hydrahive/`

### Trust boundaries

| Boundary | Trust level | Notes |
|----------|-------------|-------|
| Admin user (JWT) | Full trust | Can configure agents, approve execution modes |
| Regular user (JWT) | Limited trust | Can only interact with assigned project |
| Agent (LLM output) | Untrusted | Treated as adversarial input — blocked by sandbox |
| External LLM API | Untrusted | Prompt injection from tool outputs possible |

### Privilege Escalation Risks

**Prompt injection via tool output:** An agent reads a malicious file containing "ignore all previous instructions". HydraHive does not sanitize tool outputs before feeding them back to the LLM. Mitigation: `shell_exec` blocklist prevents the most dangerous follow-up commands.

**Execution mode escalation:** If an agent is configured with `elevated` or `root` execution mode, it has broader shell access. Never grant `root` mode to agents that process user-supplied content.

**Session fixation:** JWTs do not expire by default. Rotate `jwt_secret` to invalidate all sessions if a token is compromised.

**Path traversal in memory tools:** `read_memory`/`write_memory` tools are scoped to `/agents/<id>/memory/`. The path is validated server-side, but agent directory misconfiguration could expose other paths.

## Sandbox Guarantees

### shell_exec Blocklist

The following command patterns are **always blocked**, regardless of execution mode or agent permissions:

```
rm -rf /          # recursive delete from root
rm -rf /opt/      # delete installation
dd if=            # disk write
mkfs              # filesystem format
git -C /opt/      # git operations in install dir
systemctl stop    # stop services
systemctl disable # disable services
kill -9 1         # kill init
> /etc/           # overwrite system config
chmod -R 777 /    # mass permission change
```

The blocklist is enforced in `tool_registry.py::ShellExecTool.execute()` before any subprocess is spawned. It cannot be bypassed via execution mode.

### Execution Modes

| Mode | Shell access | Filesystem | Network |
|------|-------------|------------|---------|
| `safe` | None | Agent dir only (read) | Outbound HTTP |
| `elevated` | Scoped (`/tmp`, `/home/hydrahive`) | Agent dir (read/write) | Outbound HTTP |
| `root` | Broad (`/opt/hydrahive` excluded) | Most paths | Outbound HTTP |

`root` mode requires explicit admin approval in the Console. It is intended for DevOps agents running on trusted infrastructure, not for user-facing chatbots.

### What is NOT sandboxed

- Outbound network requests — agents can call external URLs
- Memory usage — no per-agent RAM limits
- LLM token spend — rate limiting helps but does not hard-cap per session
- Inter-agent calls — `ask_agent` / `delegate_agent` are rate-limited but not sandboxed

## Security Architecture

HydraHive is designed for self-hosted, private-network deployment. Key security properties:

- **JWT authentication** — All API endpoints require a signed JWT (secret generated at install time)
- **Role-based access control** — `admin` and `user` roles with UI and API enforcement
- **Execution mode policy** — Agent shell access is gated by `safe / elevated / root` modes; admin approval required for escalation
- **shell_exec blocklist** — Destructive commands (`rm -rf`, `mkfs`, `dd`, etc.) are blocked at the tool level regardless of agent permissions
- **Secrets externalized** — All credentials live in `/etc/hydrahive/` (not in the repo)
- **Project isolation** — Each project runs as a dedicated Linux user

## Deployment Recommendations

- Run HydraHive behind a reverse proxy (nginx) with TLS
- Do not expose port 8765 (Core API) directly — use the nginx proxy on port 80/443
- Keep `/etc/hydrahive/` readable only by the `hydrahive` user (`chmod 700`)
- Rotate the JWT secret (`/etc/hydrahive/jwt_secret`) if you suspect compromise — this invalidates all active sessions
- Use a dedicated non-root system user (`hydrahive`) as the service account

## Secret Rotation Runbook

All secrets live in `/etc/hydrahive/` (or `/etc/hydrahive/` on older installs). The service must be restarted after rotation.

### JWT Secret (invalidates all active user sessions)

```bash
sudo rm /etc/hydrahive/jwt_secret   # or /etc/hydrahive/jwt_secret
sudo systemctl restart hydrahive-core  # new secret generated on startup
# All users must log in again
```

### Internal Secret (used for agent-to-agent calls)

```bash
sudo rm /etc/hydrahive/internal_secret
sudo systemctl restart hydrahive-core
```

### API Keys / LLM credentials

Edit `/etc/hydrahive/llm_config.json` or the relevant config file directly, then:

```bash
sudo systemctl restart hydrahive-core
```

### Admin Password

Use the HydraHive Console → Admin → User Management, or update `/etc/hydrahive/users.json` directly:

```bash
# The password is stored as pbkdf2b:<salt>:<hash>
# The easiest way is via the API:
curl -X POST http://localhost:8765/admin/users/<username>/password \
  -H "Authorization: Bearer <admin-jwt>" \
  -d '{"password": "NewSecurePassword123"}'
```

### Gitea Token (for git_push tool)

Update `/etc/hydrahive/gitea_config.json`, then restart the service.

### After Any Rotation

1. Verify the service starts: `systemctl status hydrahive-core`
2. Check logs: `journalctl -u hydrahive-core -n 20`
3. Test a login via the Console UI

## Risk-Modifying Environment Variables

The following environment variables change HydraHive's default security posture.
They are intended for **local development setups only** and must never be set in
production deployments.

### `HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT=1`

- **Risk Level**: **CRITICAL** — bypasses the `#747` project-user isolation
  for `shell_exec` in `unrestricted` execution mode.
- **Effect**: When set, `shell_exec` falls back to `sudo bash -c` (runs as
  root) instead of hard-blocking the call if the `proj_<project_id>` system
  user does not exist.
- **Why it exists**: development environments where no system users are
  provisioned for each project.
- **Audit trail** (`#776`):
  - Core startup logs `AUDIT [SECURITY]` at `ERROR` level if the flag is set.
  - Every root-escape logs `AUDIT [SECURITY] shell_exec [agent] (UNRESTRICTED/ROOT ...)`
    at `ERROR` level.
  - Each root-escape writes a persistent entry to the audit log
    (`action="shell_exec_root_override"`).
- **How to remove**: `unset HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT`, remove from
  any systemd unit `Environment=` directives, then restart the service.
- **Recommended alternative**: provision project users via
  `POST /projects/{id}/provision`, or use `execution_mode=elevated` which
  runs commands inside a `bwrap`-sandbox.

*Setting this variable in a production deployment is a security
misconfiguration.*
