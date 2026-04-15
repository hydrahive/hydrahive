"""Tests für subagent_worktrees (#651)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hydrahive_core.subagent_worktrees import (
    WorktreeError,
    WorktreeMeta,
    create_worktree,
    get_worktree,
    is_git_repo,
    list_worktrees,
    release_worktree,
    remove_worktree,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo).returncode == 0
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-q", "-m", "init"], repo)


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "repo"
    _init_repo(p)
    return p


@pytest.fixture
def worktrees(tmp_path, monkeypatch):
    d = tmp_path / "wt"
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(d))
    return d


BASE_KW = dict(
    parent_project_id="proj1",
    parent_agent_id="boss",
    sub_agent_id="sub1",
    task_id="t1",
)


# ── 1. Create auf sauberem Repo ──────────────────────────────────────────────
def test_create_on_clean_repo(repo, worktrees):
    meta = create_worktree(base_repo=repo, **BASE_KW)
    assert isinstance(meta, WorktreeMeta)
    assert meta.status == "active"
    assert meta.dirty is False
    assert meta.base_branch == "main"
    assert len(meta.base_commit) == 40

    tree = Path(meta.worktree_path)
    assert tree.is_dir()
    assert (tree / "README.md").read_text() == "hello\n"

    meta_json = worktrees / "meta" / f"{meta.worktree_id}.json"
    assert meta_json.exists()
    data = json.loads(meta_json.read_text())
    assert data["worktree_id"] == meta.worktree_id
    assert data["parent_project_id"] == "proj1"
    assert data["status"] == "active"
    assert data["isolation_mode"] == "full_worktree"
    assert data["write_scope"] is None


# ── 2. Worktree-Pfad liegt unter worktrees_dir/trees ─────────────────────────
def test_worktree_path_under_root(repo, worktrees):
    meta = create_worktree(base_repo=repo, **BASE_KW)
    tree = Path(meta.worktree_path).resolve()
    trees_root = (worktrees / "trees").resolve()
    assert tree.is_relative_to(trees_root)


# ── 3. base_commit stimmt mit HEAD ───────────────────────────────────────────
def test_base_commit_matches_head(repo, worktrees):
    head = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    meta = create_worktree(base_repo=repo, **BASE_KW)
    assert meta.base_commit == head


# ── 4. Detached HEAD auf base_branch=None ────────────────────────────────────
def test_detached_head_base_branch_none(repo, worktrees):
    commit = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    _git(["checkout", "-q", "--detach", commit], repo)
    meta = create_worktree(base_repo=repo, **BASE_KW)
    assert meta.base_branch is None


# ── 5. Dirty repo ohne allow_dirty → Error ──────────────────────────────────
def test_dirty_blocks_without_allow(repo, worktrees):
    (repo / "README.md").write_text("modified\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="uncommitted changes"):
        create_worktree(base_repo=repo, **BASE_KW)
    # Keine Trees/Meta erzeugt
    assert not (worktrees / "trees").exists() or not any((worktrees / "trees").iterdir())


# ── 6. Dirty repo mit allow_dirty → OK, dirty=True persistiert ──────────────
def test_dirty_allowed(repo, worktrees):
    (repo / "README.md").write_text("modified\n", encoding="utf-8")
    meta = create_worktree(base_repo=repo, allow_dirty=True, **BASE_KW)
    assert meta.dirty is True


# ── 7. Non-git-Dir → Error ───────────────────────────────────────────────────
def test_non_git_dir(tmp_path, worktrees):
    non_git = tmp_path / "plain"
    non_git.mkdir()
    assert is_git_repo(non_git) is False
    with pytest.raises(WorktreeError, match="not a git repository"):
        create_worktree(base_repo=non_git, **BASE_KW)


# ── 8–11. Path-Traversal-Schutz ─────────────────────────────────────────────
@pytest.mark.parametrize("bad_id", ["..", "../../etc", "a/b", "../escape"])
def test_path_traversal_slashes_and_dotdot(repo, worktrees, bad_id):
    kw = {**BASE_KW, "sub_agent_id": bad_id}
    with pytest.raises(WorktreeError, match="invalid sub_agent_id"):
        create_worktree(base_repo=repo, **kw)


@pytest.mark.parametrize("bad_id", ["has space", "bad*char", "semi;colon", "", "x" * 65])
def test_invalid_identifiers(repo, worktrees, bad_id):
    kw = {**BASE_KW, "sub_agent_id": bad_id}
    with pytest.raises(WorktreeError, match="invalid"):
        create_worktree(base_repo=repo, **kw)


# ── 12. Kollisions-Retry bei gleichem Timestamp ─────────────────────────────
def test_collision_retry(repo, worktrees, monkeypatch):
    from hydrahive_core import subagent_worktrees as wm
    # Zwei IDs in Folge — zweite kollidiert mit erster, dritte ist eindeutig.
    ids_iter = iter(["wt-20260101T000000Z-sub1-aaaaaaaa",
                     "wt-20260101T000000Z-sub1-aaaaaaaa",  # Kollision
                     "wt-20260101T000000Z-sub1-bbbbbbbb"])
    monkeypatch.setattr(wm, "_build_worktree_id", lambda _s: next(ids_iter))
    meta1 = create_worktree(base_repo=repo, **BASE_KW)
    meta2 = create_worktree(base_repo=repo, **BASE_KW)
    assert meta1.worktree_id != meta2.worktree_id


# ── 13. Max-Retry erschöpft → Error ─────────────────────────────────────────
def test_collision_retry_exhausted(repo, worktrees, monkeypatch):
    from hydrahive_core import subagent_worktrees as wm
    fixed = "wt-20260101T000000Z-sub1-aaaaaaaa"
    monkeypatch.setattr(wm, "_build_worktree_id", lambda _s: fixed)
    create_worktree(base_repo=repo, **BASE_KW)
    with pytest.raises(WorktreeError, match="unique worktree_id"):
        create_worktree(base_repo=repo, **BASE_KW)


# ── 14. Haupt-Workspace bleibt unverändert ──────────────────────────────────
def test_main_workspace_untouched(repo, worktrees):
    before = (repo / "README.md").read_text()
    before_head = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    before_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()

    meta = create_worktree(base_repo=repo, **BASE_KW)
    # Modifiziere Sub-Worktree
    (Path(meta.worktree_path) / "README.md").write_text("changed in subtree\n", encoding="utf-8")

    assert (repo / "README.md").read_text() == before
    assert _git(["rev-parse", "HEAD"], repo).stdout.strip() == before_head
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip() == before_branch


# ── 15. list_worktrees ──────────────────────────────────────────────────────
def test_list_worktrees(repo, worktrees):
    m1 = create_worktree(base_repo=repo, **BASE_KW)
    m2 = create_worktree(base_repo=repo, **{**BASE_KW, "sub_agent_id": "sub2"})
    items = list_worktrees()
    ids = {m.worktree_id for m in items}
    assert m1.worktree_id in ids and m2.worktree_id in ids


def test_get_worktree(repo, worktrees):
    m = create_worktree(base_repo=repo, **BASE_KW)
    again = get_worktree(m.worktree_id)
    assert again.worktree_id == m.worktree_id
    assert again.base_commit == m.base_commit


def test_get_worktree_invalid_id():
    with pytest.raises(WorktreeError, match="invalid worktree_id"):
        get_worktree("../etc/passwd")


# ── 16. release_worktree — Tree bleibt, Status flippt ───────────────────────
def test_release_keeps_tree(repo, worktrees):
    m = create_worktree(base_repo=repo, **BASE_KW)
    tree = Path(m.worktree_path)
    assert tree.exists()
    released = release_worktree(m.worktree_id)
    assert released.status == "released"
    assert released.released_at is not None
    assert tree.exists(), "Tree soll bei release NICHT entfernt werden"
    # Meta auf Disk spiegelt Status
    disk = get_worktree(m.worktree_id)
    assert disk.status == "released"


# ── 17. remove_worktree — Tree weg, Meta bleibt mit status=removed ──────────
def test_remove_drops_tree_keeps_meta(repo, worktrees):
    m = create_worktree(base_repo=repo, **BASE_KW)
    tree = Path(m.worktree_path)
    assert tree.exists()

    removed = remove_worktree(m.worktree_id)
    assert removed.status == "removed"
    assert removed.removed_at is not None
    assert removed.tree_removed is True
    assert not tree.exists()

    # Meta-Datei noch da
    meta_json = worktrees / "meta" / f"{m.worktree_id}.json"
    assert meta_json.exists()

    # git worktree list zeigt ihn nicht mehr
    r = _git(["worktree", "list", "--porcelain"], repo)
    assert str(tree) not in r.stdout


def test_remove_already_gone_tree(repo, worktrees):
    m = create_worktree(base_repo=repo, **BASE_KW)
    # Externes Löschen des Trees + prune
    import shutil
    shutil.rmtree(Path(m.worktree_path))
    _git(["worktree", "prune"], repo)

    removed = remove_worktree(m.worktree_id)
    assert removed.status == "removed"
    assert removed.tree_removed is True


# ── 18. is_git_repo ─────────────────────────────────────────────────────────
def test_is_git_repo(tmp_path, repo):
    assert is_git_repo(repo) is True
    other = tmp_path / "not_git"
    other.mkdir()
    assert is_git_repo(other) is False
    assert is_git_repo(tmp_path / "doesnt_exist") is False


# ── 19. Settings-Property respektiert ENV ───────────────────────────────────
def test_settings_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_WORKTREES_DIR", str(tmp_path / "custom"))
    from hydrahive_core.settings import HydraHiveSettings
    s = HydraHiveSettings()
    assert s.worktrees_dir == tmp_path / "custom"


def test_settings_default_path(monkeypatch):
    monkeypatch.delenv("HYDRAHIVE_WORKTREES_DIR", raising=False)
    from hydrahive_core.settings import HydraHiveSettings
    s = HydraHiveSettings()
    assert s.worktrees_dir == Path("/var/lib/hydrahive/worktrees")


# ── #652: isolation_mode Parameter ──────────────────────────────────────────

def test_default_isolation_mode_full_worktree(repo, worktrees):
    meta = create_worktree(base_repo=repo, **BASE_KW)
    assert meta.isolation_mode == "full_worktree"


@pytest.mark.parametrize("mode", ["read_only", "patch_only", "full_worktree"])
def test_isolation_mode_persisted(repo, worktrees, mode):
    kw = {**BASE_KW, "sub_agent_id": f"sub_{mode}"}
    meta = create_worktree(base_repo=repo, isolation_mode=mode, **kw)
    assert meta.isolation_mode == mode
    # Disk-Persistenz
    disk = get_worktree(meta.worktree_id)
    assert disk.isolation_mode == mode


def test_invalid_isolation_mode(repo, worktrees):
    with pytest.raises(WorktreeError, match="invalid isolation_mode"):
        create_worktree(base_repo=repo, isolation_mode="bogus", **BASE_KW)


def test_legacy_isolation_mode_mapped_on_read(repo, worktrees):
    """Pre-#652 Meta-Dateien hatten isolation_mode='worktree'.
    Loader muss das transparent auf 'full_worktree' mappen.
    """
    meta = create_worktree(base_repo=repo, **BASE_KW)
    # Meta-JSON auf Legacy-Wert manipulieren
    meta_file = worktrees / "meta" / f"{meta.worktree_id}.json"
    data = json.loads(meta_file.read_text())
    data["isolation_mode"] = "worktree"
    meta_file.write_text(json.dumps(data))

    reread = get_worktree(meta.worktree_id)
    assert reread.isolation_mode == "full_worktree"
