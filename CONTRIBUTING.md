# Contributing to HydraHive

Thank you for your interest in contributing! HydraHive is an open-source AI agent platform — contributions of all kinds are welcome.

## Ways to contribute

- **Bug reports** — open an issue with steps to reproduce
- **Feature ideas** — open an issue describing the use case
- **Code** — fix a bug, implement a feature, improve performance
- **Documentation** — improve setup guides, add examples

## Getting started

### Prerequisites

- Python 3.12+
- Node.js 20+
- A running HydraHive instance for testing (see [install guide](installer/install.sh))

### Local setup

```bash
# Clone the repo
git clone https://github.com/hydrahive/hydrahive.git
cd hydrahive

# Install core dependencies
pip install -e "core/[dev]"

# Install console dependencies
npm install --prefix console

# Run the console in dev mode
npm run dev --prefix console
```

### Project structure

```
core/          Python backend (FastAPI)
  src/hydrahive_core/
    main.py              Entry point, router registration
    router_*.py          API route handlers
    orchestrator.py      Agent loop + tool execution
    memory_search.py     Hybrid BM25 + FAISS memory search
    semantic_index.py    FAISS embedding index
    plugin_manager.py    Plugin system

console/       React frontend (Vite + Tailwind)
  src/pages/   One file per page/view
  src/components/

agents/        Agent configurations (YAML + soul.md)
installer/     Setup scripts
scripts/       Deployment helpers
```

## Making a pull request

1. **Fork** the repository and create a branch from `main`
2. **Make your changes** — keep them focused on one thing
3. **Test** that the core starts (`python -m hydrahive_core`) and the console builds (`npm run build --prefix console`)
4. **Open a PR** with a clear description of what and why

No need for a CLA or formal process — just open the PR and we'll review it.

## Good first issues

Look for issues tagged [`good first issue`](https://github.com/hydrahive/hydrahive/issues?q=label%3A%22good+first+issue%22) — these are self-contained tasks with clear scope.

## Code style

- Python: follow existing patterns, no external formatter required
- TypeScript/React: Tailwind for styling, no CSS modules
- Keep changes minimal — don't refactor unrelated code in the same PR

## Questions?

Open an issue or leave a comment on an existing one — we're happy to help.
