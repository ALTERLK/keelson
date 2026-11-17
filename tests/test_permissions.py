"""Decision-order coverage for ``check_permission`` — the harness's safety gate."""

from __future__ import annotations

import json

import pytest

from keelson.tools import check_permission, reset_permission_cache


@pytest.fixture(autouse=True)
def _fresh_rules():
    reset_permission_cache()
    yield
    reset_permission_cache()


# --- mode x tool matrix -----------------------------------------------------

def test_bypass_allows_everything_except_what_is_refused_everywhere():
    assert check_permission("run_shell", {"command": "git push --force"}, mode="bypassPermissions")["action"] == "allow"
    # Deleting from the shell and touching the home folder are refused even under --yolo.
    assert check_permission("run_shell", {"command": "rm -rf /"}, mode="bypassPermissions")["action"] == "deny"
    assert check_permission("run_shell", {"command": "mv ~ /tmp/x"}, mode="bypassPermissions")["action"] == "deny"


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


def test_accept_edits_allows_edit_tools_but_not_irreversible_shell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.py").write_text("x", encoding="utf-8")
    assert check_permission("edit_file", {"file_path": "f.py"}, mode="acceptEdits")["action"] == "allow"
    assert check_permission("run_shell", {"command": "npm install x"}, mode="acceptEdits")["action"] == "confirm"


def test_dangerous_shell_confirms_in_default():
    r = check_permission("run_shell", {"command": "git reset --hard HEAD"}, mode="default")
    assert r["action"] == "confirm"


def test_dangerous_shell_denied_in_dont_ask():
    assert check_permission("run_shell", {"command": "git push origin main"}, mode="dontAsk")["action"] == "deny"


def test_shell_delete_is_refused_with_a_pointer_to_delete_files():
    r = check_permission("run_shell", {"command": "rm -rf build"}, mode="default")
    assert r["action"] == "deny" and "delete_files" in r["message"]


def test_skill_ops_confirm_then_deny_in_dont_ask():
    assert check_permission("skill_create", {"name": "x"}, mode="default")["action"] == "confirm"
    assert check_permission("skill_evolve", {"skill_name": "x"}, mode="default")["action"] == "confirm"
    assert check_permission("skill_create", {"name": "x"}, mode="dontAsk")["action"] == "deny"


def test_writes_in_the_project_are_not_asked_about_but_outside_it_are(in_tmp_cwd, tmp_path):
    # Inside the project a checkpoint covers it, so a new file is no reason to stop the user.
    assert check_permission("write_file", {"file_path": "new.txt"}, mode="default")["action"] == "allow"
    outside = tmp_path.parent / "elsewhere-for-keelson-test" / "x.txt"
    r = check_permission("write_file", {"file_path": str(outside)}, mode="default")
    if r["action"] == "allow":       # tmp_path's parent is the temp folder (scratch) on this machine
        outside = __import__("pathlib").Path.home() / "Desktop" / "keelson-permission-test.txt"
        r = check_permission("write_file", {"file_path": str(outside)}, mode="default")
    assert r["action"] == "confirm" and "outside the project" in r["reason"]
    assert r["rules"] == []                    # an outside path can only be allowed once


# --- settings.json allow/deny rules ---------------------------------------

def test_deny_rule_beats_allow_rule(in_tmp_cwd, monkeypatch):
    from keelson import paths

    proj = paths.project_dir()
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["run_shell(git *)"], "deny": ["run_shell(git push*)"]}
    }), encoding="utf-8")
    reset_permission_cache()
    assert check_permission("run_shell", {"command": "git status"}, mode="default")["action"] == "allow"
    assert check_permission("run_shell", {"command": "git push origin main"}, mode="default")["action"] == "deny"


def test_allow_rule_overrides_confirm_once_the_folder_is_trusted(in_tmp_cwd):
    from keelson import paths, trust

    proj = paths.project_dir()
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["skill_create", "run_shell(*)"], "deny": ["run_shell(git push*)"]}
    }), encoding="utf-8")
    reset_permission_cache()
    # A cloned repository cannot grant itself permissions...
    assert check_permission("skill_create", {"name": "x"}, mode="default")["action"] == "confirm"
    # ...but its deny rules count straight away.
    assert check_permission("run_shell", {"command": "git push"}, mode="default")["action"] == "deny"
    trust.trust()
    reset_permission_cache()
    assert check_permission("skill_create", {"name": "x"}, mode="default")["action"] == "allow"


def test_a_dangerous_command_is_never_covered_by_an_allow_rule(in_tmp_cwd):
    rules = ["run_shell(*)", "run_shell(git *)"]
    r = check_permission("run_shell", {"command": "git push --force"}, mode="default", session_rules=rules)
    assert r["action"] == "confirm" and r["rules"] == []


def test_session_rules_cover_each_part_of_a_compound_command(in_tmp_cwd):
    cmd = {"command": "npm install && git push origin main"}
    r = check_permission("run_shell", cmd, mode="default")
    assert r["action"] == "confirm"
    assert sorted(r["rules"]) == ["run_shell(git push*)", "run_shell(npm install*)"]
    assert check_permission("run_shell", cmd, mode="default", session_rules=["run_shell(npm install*)"])["action"]         == "confirm"
    assert check_permission("run_shell", cmd, mode="default", session_rules=r["rules"])["action"] == "allow"


def test_always_allow_in_project_writes_local_settings_and_trusts(in_tmp_cwd):
    from keelson import paths, trust
    from keelson.tools import save_project_rule

    save_project_rule("run_shell(npm install*)")
    data = json.loads(paths.project_local_settings_file().read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["run_shell(npm install*)"]
    assert trust.is_trusted()
    assert check_permission("run_shell", {"command": "npm install left-pad"}, mode="default")["action"] == "allow"


def test_delete_files_is_free_inside_the_project_and_refused_for_home(in_tmp_cwd):
    from pathlib import Path

    (in_tmp_cwd / "junk.txt").write_text("x", encoding="utf-8")
    assert check_permission("delete_files", {"paths": ["junk.txt"]}, mode="default")["action"] == "allow"
    r = check_permission("delete_files", {"paths": [str(Path.home())]}, mode="bypassPermissions")
    assert r["action"] == "deny" and "home folder" in r["message"]
    assert check_permission("delete_files", {"paths": ["junk.txt"]}, mode="plan")["action"] == "deny"


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
    from keelson.agent import Agent

    assert Agent(permission_mode="default")._subagent_permission_mode() == "dontAsk"
    assert Agent(permission_mode="acceptEdits")._subagent_permission_mode() == "dontAsk"
    assert Agent(permission_mode="plan")._subagent_permission_mode() == "plan"
    assert Agent(permission_mode="bypassPermissions")._subagent_permission_mode() == "bypassPermissions"


def test_subagent_mode_env_override(monkeypatch):
    monkeypatch.setenv("KEELSON_SUBAGENT_MODE", "acceptEdits")
    from keelson.agent import Agent

    assert Agent(permission_mode="default")._subagent_permission_mode() == "acceptEdits"
