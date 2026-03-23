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
