"""
test_migrate_v2.py — Tests für migrate-v2.sh (#590)

Ruft das Script via subprocess mit Fake-Verzeichnisstruktur auf.
Env-Overrides: HYDRAHIVE_AGENTS_DIR, HYDRAHIVE_PROJECTS_DIR,
               HYDRAHIVE_BACKUP_DIR, HYDRAHIVE_USERS_JSON, HYDRAHIVE_VENV
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "migrate-v2.sh"


def run_migrate(tmp_path, agents_content: dict, users: dict | None = None, dry_run: bool = True):
    """
    Richtet eine Fake-Verzeichnisstruktur ein und ruft migrate-v2.sh auf.

    agents_content: {agent_id: {"agent_yaml": {...}, "soul_md": str, "memory": {filename: content}}}
    users: users.json-Inhalt als dict (username → {"allowed_projects": [...]})
    Gibt (returncode, stdout, projects_dir) zurück.

    Idempotent-safe: Verzeichnisse werden mit exist_ok=True angelegt, damit
    derselbe tmp_path mehrfach verwendet werden kann (Idempotenz-Test).
    """
    agents_dir = tmp_path / "agents"
    projects_dir = tmp_path / "projects"
    backup_dir = tmp_path / "backup"
    users_json = tmp_path / "users.json"

    agents_dir.mkdir(exist_ok=True)
    projects_dir.mkdir(exist_ok=True)

    # Agent-Verzeichnisse anlegen
    for agent_id, content in agents_content.items():
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(exist_ok=True)

        if "agent_yaml" in content:
            with open(agent_dir / "agent.yaml", "w") as f:
                yaml.dump(content["agent_yaml"], f)

        if "soul_md" in content:
            (agent_dir / "soul.md").write_text(content["soul_md"])

        if "memory" in content:
            mem_dir = agent_dir / "memory"
            mem_dir.mkdir(exist_ok=True)
            for fname, fcontent in content["memory"].items():
                (mem_dir / fname).write_text(fcontent)

    # users.json
    users_json.write_text(json.dumps(users or {}))

    env = {
        "HYDRAHIVE_AGENTS_DIR": str(agents_dir),
        "HYDRAHIVE_PROJECTS_DIR": str(projects_dir),
        "HYDRAHIVE_BACKUP_DIR": str(backup_dir),
        "HYDRAHIVE_USERS_JSON": str(users_json),
        "HYDRAHIVE_VENV": str(Path(sys.executable).parent.parent),
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
    }

    args = ["bash", str(SCRIPT)]
    if dry_run:
        args.append("--dry-run")

    result = subprocess.run(args, env=env, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr, projects_dir


def minimal_agent_yaml(model="claude-sonnet-4-6", exec_mode="safe"):
    return {
        "id": "test_agent",
        "identity": "Test Agent",
        "description": "Ein Testagent",
        "llm": {"model": model, "temperature": 0.7, "max_tokens": 4096},
        "execution_modes": {"default": exec_mode},
    }


# ── P3.1: Disabled-Agent wird übersprungen ───────────────────────────────────

def test_disabled_agent_wird_uebersprungen(tmp_path):
    rc, out, projects_dir = run_migrate(tmp_path, {
        "_mein_agent_disabled": {"agent_yaml": minimal_agent_yaml()},
    })
    assert rc == 0
    assert "disabled" in out.lower() or "SKIP" in out or "uebersprungen" in out.lower()
    # Kein Projekt angelegt
    assert not any(projects_dir.iterdir())


# ── P3.2: Ephemeral-Agent wird übersprungen ──────────────────────────────────

def test_ephemeral_agent_wird_uebersprungen(tmp_path):
    rc, out, projects_dir = run_migrate(tmp_path, {
        "file_specialist_a1b2c3d4": {"agent_yaml": minimal_agent_yaml()},
    })
    assert rc == 0
    assert "ephemeral" in out.lower() or "uebersprungen" in out.lower()
    assert not any(projects_dir.iterdir())


# ── P3.3: Agent ohne agent.yaml wird übersprungen ────────────────────────────

def test_kein_agent_yaml_wird_uebersprungen(tmp_path):
    agents_dir = tmp_path / "agents" / "naked_agent"
    agents_dir.mkdir(parents=True)
    (tmp_path / "projects").mkdir()
    (tmp_path / "users.json").write_text("{}")

    env = {
        "HYDRAHIVE_AGENTS_DIR": str(tmp_path / "agents"),
        "HYDRAHIVE_PROJECTS_DIR": str(tmp_path / "projects"),
        "HYDRAHIVE_BACKUP_DIR": str(tmp_path / "backup"),
        "HYDRAHIVE_USERS_JSON": str(tmp_path / "users.json"),
        "HYDRAHIVE_VENV": str(Path(sys.executable).parent.parent),
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
    }
    result = subprocess.run(["bash", str(SCRIPT), "--dry-run"], env=env, capture_output=True, text=True)
    out = result.stdout + result.stderr
    assert "keine agent.yaml" in out or "uebersprungen" in out.lower()
    assert not any((tmp_path / "projects").iterdir())


# ── P3.4: Idempotenz — config.yaml existiert → überspringen ─────────────────

def test_idempotent_wenn_config_yaml_existiert(tmp_path):
    agent = {"agent_yaml": minimal_agent_yaml(), "soul_md": "# Test"}
    # Erst einmal richtig migrieren
    rc1, out1, projects_dir = run_migrate(tmp_path, {"my_agent": agent}, dry_run=False)
    assert rc1 == 0
    assert (projects_dir / "my_agent" / "config.yaml").exists()

    # Zweiter Lauf → idempotent
    rc2, out2, _ = run_migrate(tmp_path, {"my_agent": agent}, dry_run=False)
    assert rc2 == 0
    assert "existiert bereits" in out2 or "uebersprungen" in out2.lower()


# ── P3.5: users.json → members korrekt gemappt ───────────────────────────────

def test_users_json_members_korrekt(tmp_path):
    users = {
        "alice": {"allowed_projects": ["my_agent", "other"]},
        "bob": {"allowed_projects": ["my_agent"]},
        "charlie": {"allowed_projects": ["other_project"]},
    }
    agent = {"agent_yaml": minimal_agent_yaml(), "soul_md": "# Soul"}
    rc, out, projects_dir = run_migrate(tmp_path, {"my_agent": agent}, users=users, dry_run=False)
    assert rc == 0

    config_path = projects_dir / "my_agent" / "config.yaml"
    assert config_path.exists()
    config = yaml.safe_load(config_path.read_text())
    members = config.get("members", [])
    assert "alice" in members
    assert "bob" in members
    assert "charlie" not in members


# ── P3.6: llm.model → provider korrekt abgeleitet ───────────────────────────

@pytest.mark.parametrize("model,expected_provider", [
    ("gpt-4o", "openai"),
    ("gpt-3.5-turbo", "openai"),
    ("claude-sonnet-4-6", "anthropic"),
    ("claude-opus-4-6", "anthropic"),
    ("gemini-pro", "google"),
])
def test_provider_aus_model_abgeleitet(tmp_path, model, expected_provider):
    agent = {"agent_yaml": minimal_agent_yaml(model=model), "soul_md": "# Test"}
    # tmp_path ist pro parametrize-Fall eindeutig
    rc, out, projects_dir = run_migrate(tmp_path, {"test_agent": agent}, dry_run=False)
    assert rc == 0, f"Script fehlgeschlagen: {out}"

    config = yaml.safe_load((projects_dir / "test_agent" / "config.yaml").read_text())
    assert config["llm"]["provider"] == expected_provider, (
        f"Erwartet '{expected_provider}' für Model '{model}', "
        f"bekommen: '{config['llm']['provider']}'"
    )


# ── P3.7: soul.md → AGENT.md + memory/ kopiert ───────────────────────────────

def test_soul_md_und_memory_werden_kopiert(tmp_path):
    agent = {
        "agent_yaml": minimal_agent_yaml(),
        "soul_md": "# Meine Seele\n\nIch bin ein Agent.",
        "memory": {
            "project_notes.md": "# Notizen\n\nWichtige Info",
            "MEMORY.md": "- [project_notes.md](project_notes.md) — Notizen",
        },
    }
    rc, out, projects_dir = run_migrate(tmp_path, {"my_agent": agent}, dry_run=False)
    assert rc == 0

    agent_md = projects_dir / "my_agent" / "AGENT.md"
    assert agent_md.exists()
    assert "Meine Seele" in agent_md.read_text()

    mem_dir = projects_dir / "my_agent" / "memory"
    assert (mem_dir / "project_notes.md").exists()
    assert "Wichtige Info" in (mem_dir / "project_notes.md").read_text()
    assert (mem_dir / "MEMORY.md").exists()


# ── P3.8: execution_mode wird korrekt übernommen ─────────────────────────────

@pytest.mark.parametrize("exec_mode", ["safe", "elevated", "unrestricted"])
def test_execution_mode_uebernommen(tmp_path, exec_mode):
    agent = {"agent_yaml": minimal_agent_yaml(exec_mode=exec_mode)}
    rc, out, projects_dir = run_migrate(tmp_path, {"mode_agent": agent}, dry_run=False)
    assert rc == 0

    config = yaml.safe_load((projects_dir / "mode_agent" / "config.yaml").read_text())
    assert config["execution_mode"] == exec_mode
