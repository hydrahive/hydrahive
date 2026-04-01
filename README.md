# HydraHive

**Self-hosted AI Agent Platform** — Multi-agent swarms, Matrix communication, project isolation.

> Install Linux → Install HydraHive → Manage everything through the web console.

---

## Features

- **Multi-Agent Swarms** — Boss agent coordinates worker agents in parallel
- **Project Isolation** — Each project gets its own Linux user, Samba share and Matrix room
- **Multi-LLM** — Ollama (local), Claude (OAuth), OpenAI — configurable per agent with fallback chains
- **Matrix Integration** — Agents are real Matrix bots; intervene directly via Element
- **Discord Integration** — Personal agents can read and respond on Discord
- **Web Search** — Built-in SearXNG metasearch engine (no API key, no tracking)
- **QMD Skills** — Learned knowledge in Markdown files with YAML frontmatter
- **Personal Agent** — Every user gets their own private agent (`personal_<username>`)
- **Memory System** — Agents store knowledge persistently in Markdown files, auto-injected into system prompt
- **A-MEM Shared Memory** — Central cross-agent knowledge base for errors, solutions and learnings
- **Workstation Access** — Personal agents connect via SSH/SFTP to the user's workstation
- **Git Tools** — Agents can commit, push and create pull requests on Gitea
- **MCP Servers** — Attach external tool servers via streamableHttp (e.g. QMD Memory Search)
- **Execution Modes** — safe/elevated/root for controlled privilege escalation
- **Web Console** — Full management without SSH: agents, projects, users, logs, skills, MCP, search
- **Streaming** — Responses appear token by token with stop-button interrupt
- **Webhook System** — External triggers for agents (`/hooks/{project}/wake`)
- **Audit Log** — All user actions logged
- **One-Click Update** — Update from the web console (git pull + build + restart)

## Deployment Profiles

| Profile | GPU | LLM | Good for |
|---------|-----|-----|----------|
| **Lite** | No | Cloud APIs | VPS, testing, demo |
| **Full** | Yes (PCIe passthrough) | Ollama + Cloud | Production, full control |

Reference setup: GTX 1080 Ti (11 GB VRAM) on Proxmox VM, Ubuntu 24.04

## Quick Start

```bash
git clone https://github.com/hydrahive/hydrahive.git
cd hydrahive
sudo bash installer/install.sh
# → open https://<IP> → Setup Wizard
```

## Documentation

| Document | Content |
|----------|---------|
| [Handbook](docs/handbook.md) | Installation, getting started, all features |
| [Technical Docs](docs/technical.md) | Architecture, modules, data flow |
| [API Reference](docs/api-reference.md) | All REST endpoints |
| [Developer Guide](docs/development.md) | Tools, skills, endpoints, adding console pages |

## Architecture

```
Browser (React) → nginx (HTTPS) → FastAPI Core → Orchestrator
                                              ↓
                                   Boss Agent → Worker Agents
                                              ↓
                                   conduwuit (Matrix) ← Element
```

## Stack

- **Core:** Python 3.12, FastAPI, litellm, matrix-nio, Anthropic SDK
- **Console:** React 18, TypeScript, Vite, Tailwind CSS
- **Matrix:** conduwuit (Rust, single binary, RocksDB)
- **Search:** SearXNG (native, no Docker)
- **LLM:** Ollama + Anthropic OAuth + OpenAI
- **Installer:** Bash + systemd (no Docker)

## Status

🚧 Active development — stable core, features may change

## License

MIT
