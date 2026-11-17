"""Shared fixtures.

Every test runs with an isolated ``KEELSON_HOME`` and a scratch cwd so nothing
touches the developer's real ``~/.keelson`` and so permission-rule / settings
lookups are deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "keelson-home"
    home.mkdir()
    monkeypatch.setenv("KEELSON_HOME", str(home))
    monkeypatch.setenv("PYTHONUTF8", "1")
    for var in (
        "KEELSON_AUTO_SKILL_EVOLUTION",
        "KEELSON_AUTO_SKILL_TARGET",
        "KEELSON_PROFILE",       # a developer's own `keelson -P` / profile must not leak into tests
        "KEELSON_SHELL",
        "KEELSON_TRUST_ALL",
    ):
        monkeypatch.delenv(var, raising=False)
    # Snapshotting the cwd on every scripted turn would be slow and is not what
    # most tests are about; tests/test_checkpoints.py turns it back on.
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "0")
    # Same for end-of-turn checks, which would run the project's tests; tests/test_verify.py turns them on.
    monkeypatch.setenv("KEELSON_VERIFY", "0")
    # Interface text in English whatever the machine's language; tests/test_terminal_ux.py covers Chinese.
    monkeypatch.setenv("KEELSON_LANG", "en")
    monkeypatch.setenv("KEELSON_SIMPLE_INPUT", "1")
    import keelson.checkpoints as checkpoints

    checkpoints.reset()

    # Reset module-level caches that depend on cwd / env.
    import keelson.tools as tools

    tools.reset_permission_cache()
    yield
    tools.reset_permission_cache()
    checkpoints.reset()


@pytest.fixture
def in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture(scope="session")
def toolhop_tasks():
    """The real ToolHop dataset if it is present, else skip the test."""
    from keelson.bench.toolhop import default_dataset_path, load_toolhop

    path = default_dataset_path()
    if not path.exists():
        pytest.skip("no ToolHop dataset or subset present")
    return load_toolhop(path)
