"""`keelson doctor` / `keelson init`: the checks and the .env that gets written."""

from __future__ import annotations

import builtins

import pytest

from agents import doctor


def _by_id(checks):
    return {check["id"]: check for check in checks}


def _configure(monkeypatch, *, key, reachable=True, detail="model answered (ok)"):
    monkeypatch.setattr(doctor, "_resolved_config", lambda: ("https://example.invalid/v1", key, True, "some-model"))
    pings = []

    async def fake_ping(base, api_key, use_openai, model, timeout=20.0):
        pings.append(model)
        return reachable, detail

    monkeypatch.setattr(doctor, "_ping", fake_ping)
    return pings


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KEELSON_HOME", str(tmp_path / "home"))


async def test_a_working_setup_has_no_failures(monkeypatch):
    pings = _configure(monkeypatch, key="k" * 40)
    checks = _by_id(await doctor.run_checks())

    assert checks["key"]["status"] == doctor.OK
    assert checks["model"]["status"] == doctor.OK
    assert pings == ["some-model"]
    # The key itself is never echoed, only its length.
    assert "k" * 40 not in checks["key"]["detail"]
    assert "40" in checks["key"]["detail"]


async def test_no_key_is_a_failure_with_a_fix_and_no_request_is_made(monkeypatch):
    pings = _configure(monkeypatch, key=None)
    checks = _by_id(await doctor.run_checks())

    assert checks["key"]["status"] == doctor.FAIL
    assert "keelson init" in checks["key"]["fix"]
    assert "model" not in checks and pings == []


async def test_a_key_the_provider_rejects_fails_the_model_check(monkeypatch):
    _configure(monkeypatch, key="wrong", reachable=False, detail="AuthenticationError: 401")
    checks = _by_id(await doctor.run_checks())

    assert checks["key"]["status"] == doctor.OK          # it is configured...
    assert checks["model"]["status"] == doctor.FAIL       # ...but it does not work
    assert "401" in checks["model"]["detail"]
    assert checks["model"]["fix"]


async def test_offline_skips_the_network(monkeypatch):
    pings = _configure(monkeypatch, key="k" * 40)
    checks = _by_id(await doctor.run_checks(ping_model=False))
    assert "model" not in checks and pings == []


async def test_a_missing_optional_tool_is_a_warning_not_a_failure(monkeypatch):
    _configure(monkeypatch, key="k" * 40)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    checks = _by_id(await doctor.run_checks(ping_model=False))
    assert checks["rg"]["status"] == doctor.WARN
    assert checks["git"]["status"] == doctor.FAIL


def test_exit_code_reflects_failures(monkeypatch, capsys):
    _configure(monkeypatch, key=None)
    assert doctor.doctor_main(["--offline"]) == 1
    _configure(monkeypatch, key="k" * 40)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    assert doctor.doctor_main(["--offline"]) == 0
    assert "Ready" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("openai", ["OPENAI_API_KEY=secret", "OPENAI_BASE_URL=https://h/v1"]),
        ("anthropic-native", ["ANTHROPIC_API_KEY=secret", "ANTHROPIC_BASE_URL=https://h/v1"]),
        ("anthropic", ["APIKEY=secret", "API=https://h/v1"]),
    ],
)
def test_render_env_uses_the_variables_each_protocol_reads(kind, expected):
    text = doctor.render_env({"kind": kind}, "https://h/v1", "m1", "secret")
    lines = text.splitlines()
    for line in expected:
        assert line in lines
    assert "MODEL=m1" in lines


def test_init_writes_env_then_runs_the_checks(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    answers = iter(["1", "https://gateway.invalid/v1", "my-model"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    monkeypatch.setattr(doctor.getpass, "getpass", lambda prompt="": "secret-key")
    ran = []
    monkeypatch.setattr(doctor, "doctor_main", lambda argv=None: ran.append(argv) or 0)

    assert doctor.init_main([]) == 0
    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=secret-key" in written
    assert "OPENAI_BASE_URL=https://gateway.invalid/v1" in written
    assert "MODEL=my-model" in written
    assert ran == [[]]


def test_init_does_not_overwrite_without_consent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MODEL=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert doctor.init_main([]) == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "MODEL=keep-me\n"
