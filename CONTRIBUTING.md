# Contributing to OctopOS

Thanks for your interest in contributing!

## Getting Started

1. Fork the repository and clone locally
2. Set up a local VM or test environment (see `installer/install.sh`)
3. Copy `scripts/octopos.conf.example` → `scripts/octopos.conf` and fill in your VM details
4. Use `./scripts/octopos-update.sh` to deploy changes to your test VM

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
website/    Static landing page (octopos.luckydevs.net)
```

## Conventions

- **Python**: follow existing style (no strict linter enforced yet); type hints where practical
- **TypeScript**: strict mode, no `any` without comment
- **Commits**: conventional-ish messages — `feat:`, `fix:`, `security:`, `docs:`
- **Docs**: if your change affects user-facing behavior, update `docs/handbook.md` in the same commit

## What to contribute

Good first areas:
- Bug fixes and edge-case handling
- New LLM provider integrations (OpenAI-compatible APIs)
- MCP server examples / skill packs
- Documentation improvements

Please open an issue before starting large features so we can discuss the design first.

## Security

Do not include credentials, private IPs, or API keys in your PR. See [SECURITY.md](SECURITY.md) for the secrets architecture. Use the `octopos.conf` pattern for local config that should never be committed.
