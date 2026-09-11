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
    ):
        monkeypatch.delenv(var, raising=False)

    # Reset module-level caches that depend on cwd / env.
    import agents.tools as tools

    tools.reset_permission_cache()
    yield
    tools.reset_permission_cache()


@pytest.fixture
def in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture(scope="session")
def toolhop_tasks():
    """The real ToolHop dataset if it is present, else skip the test."""
    from agents.bench.toolhop import default_dataset_path, load_toolhop

    path = default_dataset_path()
    if not path.exists():
        pytest.skip("no ToolHop dataset or subset present")
    return load_toolhop(path)
