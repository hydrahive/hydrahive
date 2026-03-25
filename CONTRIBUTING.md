# Contributing to HydraHive

Thanks for your interest in contributing!

## Getting Started

1. Fork the repository and clone locally
2. Set up a local VM or test environment (see `installer/install.sh`)
3. Copy `scripts/hydrahive.conf.example` → `scripts/hydrahive.conf` and fill in your VM details
4. Use `./scripts/hydrahive-update.sh` to deploy changes to your test VM

## Development Setup

**Core (Python / FastAPI)**
```bash
cd core
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Console (React / Vite)**
```bash
cd console
npm install
npm run dev   # dev server with HMR on :5173
```

The console dev server proxies API calls to `http://localhost:8765` — point that to your test VM via SSH tunnel or edit `vite.config.ts`.

## Project Structure

```
core/       FastAPI backend + all agent logic
console/    React frontend (Vite + Tailwind)
installer/  install.sh / update.sh for VM setup
scripts/    Dev helper scripts (deploy, backup)
docs/       Handbook (handbook.md → handbuch.html)
website/    Static landing page (hydrahive.luckydevs.net)
```

## Conventions

- **Python**: follow existing style (no strict linter enforced yet); type hints where practical
- **TypeScript**: strict mode, no `any` without comment
- **Docs**: if your change affects user-facing behavior, update `docs/handbook.md` in the same commit

## Commit Message Format

We follow a conventional commit style. Every commit message must have the form:

```
<type>(<scope>): <short description>

[optional body — what and why, not how]
[optional footer — BREAKING CHANGE: ...]
```

**Allowed types:**

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or fixing tests |
| `docs` | Documentation only |
| `security` | Security hardening or vulnerability fix |
| `perf` | Performance improvement |
| `ci` | CI/CD pipeline changes |
| `chore` | Build tooling, dependency updates, maintenance |

**Scope** (optional but encouraged): the module or area affected, e.g. `orchestrator`, `session`, `console`, `installer`, `rate-limiter`.

**Examples:**

```
feat(orchestrator): add streaming failover for OAuth models

fix(session): get_or_create returned coroutine instead of Session

security: block rm -rf and mkfs in shell_exec tool

docs: add A-MEM architecture section to DEPLOYMENT.md

refactor(#19): split orchestrator.py into 4 focused modules
```

**Breaking changes** must be marked in the footer:
```
feat(api): rename /admin/agents → /admin/agent-configs

BREAKING CHANGE: all clients using /admin/agents must update their URLs
```

## Atomic Commits

Each commit should represent a single logical change. The goal is that every commit on `main` is deployable and passes CI.

**Do:**
- One commit per feature / bug fix
- Include related test and doc changes in the same commit as the code change
- `feat: add X` + `tests/test_x.py` + `docs/handbook.md` update — all in one commit

**Don't:**
- Mix unrelated changes ("fix login + refactor session manager + update README")
- Commit broken/half-done code to `main` — use a feature branch
- Commit generated files (`dist/`, `__pycache__/`, `*.lock` changes without actual dependency changes)

**Why:**
- `git bisect` works reliably — each commit can be tested independently
- Code review is easier when commits are focused
- Rollbacks are clean: `git revert <commit>` reverts exactly one logical change

## What to contribute

Good first areas:
- Bug fixes and edge-case handling
- New LLM provider integrations (OpenAI-compatible APIs)
- MCP server examples / skill packs
- Documentation improvements

Please open an issue before starting large features so we can discuss the design first.

## Security

Do not include credentials, private IPs, or API keys in your PR. See [SECURITY.md](SECURITY.md) for the secrets architecture. Use the `hydrahive.conf` pattern for local config that should never be committed.
