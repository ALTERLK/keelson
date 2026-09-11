"""Decision-order coverage for ``check_permission`` — the harness's safety gate."""

from __future__ import annotations

import json

import pytest

from agents.tools import check_permission, reset_permission_cache


@pytest.fixture(autouse=True)
def _fresh_rules():
    reset_permission_cache()
    yield
    reset_permission_cache()


# --- mode x tool matrix -----------------------------------------------------

def test_bypass_allows_everything():
    assert check_permission("run_shell", {"command": "rm -rf /"}, mode="bypassPermissions")["action"] == "allow"


@pytest.mark.parametrize("tool", ["read_file", "list_files", "grep_search", "compact_context"])
@pytest.mark.parametrize("mode", ["default", "plan", "acceptEdits", "dontAsk"])
def test_read_tools_always_allowed(tool, mode):
    assert check_permission(tool, {"path": "x"}, mode=mode)["action"] == "allow"


def test_plan_mode_blocks_shell():
    assert check_permission("run_shell", {"command": "ls"}, mode="plan")["action"] == "deny"


def test_plan_mode_blocks_edits_except_plan_file(tmp_path):
    plan = str(tmp_path / "plan.md")
    assert check_permission("write_file", {"file_path": plan}, mode="plan", plan_file_path=plan)["action"] == "allow"
    assert check_permission("write_file", {"file_path": str(tmp_path / "other.py")},
                            mode="plan", plan_file_path=plan)["action"] == "deny"


def test_accept_edits_allows_edit_tools_but_not_shell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.py").write_text("x", encoding="utf-8")
    assert check_permission("edit_file", {"file_path": "f.py"}, mode="acceptEdits")["action"] == "allow"
    assert check_permission("run_shell", {"command": "rm foo"}, mode="acceptEdits")["action"] == "confirm"


def test_dangerous_shell_confirms_in_default():
    r = check_permission("run_shell", {"command": "git reset --hard HEAD"}, mode="default")
    assert r["action"] == "confirm"


def test_dangerous_shell_denied_in_dont_ask():
    assert check_permission("run_shell", {"command": "rm -rf build"}, mode="dontAsk")["action"] == "deny"


def test_skill_ops_confirm_then_deny_in_dont_ask():
    assert check_permission("skill_create", {"name": "x"}, mode="default")["action"] == "confirm"
    assert check_permission("skill_evolve", {"skill_name": "x"}, mode="default")["action"] == "confirm"
    assert check_permission("skill_create", {"name": "x"}, mode="dontAsk")["action"] == "deny"


def test_write_new_file_confirms_but_existing_allows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert check_permission("write_file", {"file_path": "new.txt"}, mode="default")["action"] == "confirm"
    (tmp_path / "old.txt").write_text("hi", encoding="utf-8")
    assert check_permission("write_file", {"file_path": "old.txt"}, mode="default")["action"] == "allow"


# --- settings.json allow/deny rules ---------------------------------------

def test_deny_rule_beats_allow_rule(in_tmp_cwd, monkeypatch):
    from agents import paths

    proj = paths.project_dir()
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["run_shell(git *)"], "deny": ["run_shell(git push*)"]}
    }), encoding="utf-8")
    reset_permission_cache()
    assert check_permission("run_shell", {"command": "git status"}, mode="default")["action"] == "allow"
    assert check_permission("run_shell", {"command": "git push origin main"}, mode="default")["action"] == "deny"


def test_allow_rule_overrides_confirm(in_tmp_cwd):
    from agents import paths

    proj = paths.project_dir()
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["skill_create"]}
    }), encoding="utf-8")
    reset_permission_cache()
    assert check_permission("skill_create", {"name": "x"}, mode="default")["action"] == "allow"


# --- Stage 5.2: un-classified / MCP tools + sub-agent mode --------------

@pytest.mark.parametrize("mode,expected", [
    ("plan", "deny"),
    ("default", "confirm"),
    ("acceptEdits", "confirm"),
    ("dontAsk", "deny"),
])
def test_mcp_tool_is_gated(mode, expected):
    assert check_permission("mcp__playwright__click", {}, mode=mode)["action"] == expected


def test_mcp_tool_allowed_only_under_bypass():
    assert check_permission("mcp__x__y", {}, mode="bypassPermissions")["action"] == "allow"


def test_unknown_tool_gated_like_mcp():
    assert check_permission("some_future_tool", {}, mode="default")["action"] == "confirm"
    assert check_permission("some_future_tool", {}, mode="plan")["action"] == "deny"


def test_known_tools_still_pass_through():
    assert check_permission("read_file", {"file_path": "x"}, mode="default")["action"] == "allow"
    assert check_permission("run_shell", {"command": "ls"}, mode="default")["action"] == "allow"
    assert check_permission("tool_search", {}, mode="default")["action"] == "allow"


def test_subagent_permission_mode_never_exceeds_parent(monkeypatch):
    monkeypatch.delenv("KEELSON_SUBAGENT_MODE", raising=False)
    from agents.agent import Agent

    assert Agent(permission_mode="default")._subagent_permission_mode() == "dontAsk"
    assert Agent(permission_mode="acceptEdits")._subagent_permission_mode() == "dontAsk"
    assert Agent(permission_mode="plan")._subagent_permission_mode() == "plan"
    assert Agent(permission_mode="bypassPermissions")._subagent_permission_mode() == "bypassPermissions"


def test_subagent_mode_env_override(monkeypatch):
    monkeypatch.setenv("KEELSON_SUBAGENT_MODE", "acceptEdits")
    from agents.agent import Agent

    assert Agent(permission_mode="default")._subagent_permission_mode() == "acceptEdits"
