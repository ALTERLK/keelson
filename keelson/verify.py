"""Verified completion: "done" is checked by the runtime, not taken from the model.

The failure this answers: an agent says "all tests pass" when it never ran
them, ran them and they failed, or made them pass by editing the tests. Users
of Claude Code report the first two often enough to have a name for it; the
third ("reward hacking") is measured for Claude Code and Codex by the
EvilGenie benchmark.

What Keelson does about it, all outside the model:

* **Which commands check this project.** A ``## Verify`` section in
  ``KEELSON.md`` if there is one (``/verify init`` writes it); otherwise
  detected from the project files (pytest, ``npm test``, ``cargo test``,
  ``go test``). See :func:`commands_for`.
* **Evidence comes from what actually ran.** Every tool call of the turn is
  recorded with its real exit code (:class:`Evidence`). The model's words are
  not evidence.
* **At the end of a turn that changed code**, if no check ran successfully
  after the last change, the runtime runs the checks itself. A failure goes
  back to the model with the real output, a limited number of times; after
  that the turn ends marked as failed, with the output.
* **A badge on every turn that changed files**: passed / failed / not
  verified (and why). A sentence claiming tests pass without a passing run to
  back it is flagged as an unproven claim.
* **Tests are guarded.** Changing an existing test file when the task is not
  about tests needs a person's OK, and changes that weaken tests (deleted
  tests, new skips, fewer assertions, ``assert True``, a changed expected
  value) are detected from the diff and reported.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .platforms import IS_WINDOWS

VERIFY_HEADING = re.compile(r"^#{1,6}\s*(verify|verification|验证)\b.*$", re.IGNORECASE)
DEFAULT_TIMEOUT_S = 300
DEFAULT_RETRIES = 2


# ─── which commands check this project ───────────────────────


def _venv_tool(ws: Path, name: str) -> str | None:
    for venv in (".venv", "venv"):
        exe = ws / venv / ("Scripts" if IS_WINDOWS else "bin") / (name + (".exe" if IS_WINDOWS else ""))
        if exe.is_file():
            sep = "\\" if IS_WINDOWS else "/"
            return f".{sep}{venv}{sep}{'Scripts' if IS_WINDOWS else 'bin'}{sep}{name}" + (".exe" if IS_WINDOWS else "")
    return None


def _uses_pytest(ws: Path) -> bool:
    for name in ("pytest.ini", "conftest.py", "tox.ini"):
        if (ws / name).is_file():
            return True
    for name in ("pyproject.toml", "setup.cfg"):
        f = ws / name
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            if "pytest" in text:
                return True
    for folder in ("tests", "test"):
        d = ws / folder
        if d.is_dir() and (any(d.glob("test_*.py")) or any(d.glob("*_test.py"))):
            return True
    return False


def detect(ws: Path | None = None) -> list[str]:
    """Test commands guessed from the project files. Lint and type checks only when written in KEELSON.md."""
    ws = ws or Path.cwd()
    found: list[str] = []
    if _uses_pytest(ws):
        python = _venv_tool(ws, "python") or "python"
        found.append(f"{python} -m pytest -q")
    package = ws / "package.json"
    if package.is_file():
        try:
            test = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
        except (ValueError, AttributeError):
            test = ""
        if test and "no test specified" not in test:
            runner = "pnpm" if (ws / "pnpm-lock.yaml").exists() else "yarn" if (ws / "yarn.lock").exists() else "npm"
            found.append(f"{runner} test")
    if (ws / "Cargo.toml").is_file():
        found.append("cargo test")
    if (ws / "go.mod").is_file():
        found.append("go test ./...")
    return found


def keelson_md(ws: Path | None = None) -> Path:
    return (ws or Path.cwd()) / "KEELSON.md"


def parse_verify_section(text: str) -> list[str] | None:
    """Commands under a ``## Verify`` heading (backticked list items or a code block); None: no such heading."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if VERIFY_HEADING.match(line.strip()):
            commands: list[str] = []
            fenced = False
            for body in lines[i + 1:]:
                stripped = body.strip()
                if stripped.startswith("```"):
                    fenced = not fenced
                    continue
                if fenced:
                    if stripped and not stripped.startswith("#"):
                        commands.append(stripped)
                    continue
                if re.match(r"^#{1,6}\s", stripped):
                    break
                m = re.match(r"^[-*]\s+`([^`]+)`", stripped)
                if m:
                    commands.append(m.group(1).strip())
            return commands
    return None


def commands_for(ws: Path | None = None) -> tuple[list[str], str]:
    """(commands, where they came from): KEELSON.md's Verify section, else detection."""
    ws = ws or Path.cwd()
    md = keelson_md(ws)
    if md.is_file():
        listed = parse_verify_section(md.read_text(encoding="utf-8", errors="replace"))
        if listed is not None:
            return listed, "KEELSON.md"
    found = detect(ws)
    return found, "detected" if found else "none"


def write_verify_section(commands: list[str], ws: Path | None = None) -> Path:
    """``/verify init``: add (or replace) the Verify section of KEELSON.md."""
    md = keelson_md(ws)
    section = ["## Verify", "",
               "Commands Keelson runs to check a change before it calls a turn done. One per line; edit freely.", ""]
    section += [f"- `{c}`" for c in commands] or ["No command was found. Add one as a list item, e.g. - `npm test`"]
    block = "\n".join(section) + "\n"
    if not md.exists():
        md.write_text("# KEELSON.md\n\nNotes for Keelson about this project.\n\n" + block, encoding="utf-8")
        return md
    text = md.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if VERIFY_HEADING.match(line.strip()):
            end = next((j for j in range(i + 1, len(lines)) if re.match(r"^#{1,6}\s", lines[j].strip())), len(lines))
            new = "\n".join(lines[:i]) + ("\n" if i else "") + block + ("\n" + "\n".join(lines[end:]) if end < len(lines) else "")
            md.write_text(new.rstrip("\n") + "\n", encoding="utf-8")
            return md
    md.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")
    return md


# ─── what ran ────────────────────────────────────────────────

_FAILED = re.compile(r"^Command failed \(exit code (-?\d+)\)")


def exit_code_of(result: str) -> int | None:
    """The exit code in a run_shell result (see tools._format_shell_result); None: it never ran to the end."""
    m = _FAILED.match(result)
    if m:
        return int(m.group(1))
    if result.startswith(("Command timed out", "Command not run", "Error:", "Action denied", "User denied")):
        return None
    return 0


def summarize(output: str, exit_code: int | None) -> str:
    """One line a person recognises: '42 passed', 'test result: ok. 3 passed', ..."""
    patterns = (                                                   # most specific first
        r"test result: ([^\n]+)",                                  # cargo
        r"Tests:\s+([^\n]+)",                                      # jest
        r"Test Files\s+([^\n]+)",                                  # vitest
        r"(\d+ passing[^\n]*)",                                    # mocha
        r"(\d+ (?:passed|failed|error)[^\n=]*)",                   # pytest
    )
    for pattern in patterns:
        found = re.findall(pattern, output)
        if found:
            return found[-1].strip(" =")
    if exit_code is None:
        return "did not finish"
    return "exit code 0" if exit_code == 0 else f"exit code {exit_code}"


def tail(output: str, lines: int = 60, chars: int = 4000) -> str:
    kept = "\n".join(output.rstrip().splitlines()[-lines:])
    return kept[-chars:]


_TESTISH = re.compile(r"\b(pytest|unittest|tox|nox|jest|vitest|mocha|ava|karma|cargo test|go test|"
                      r"(npm|pnpm|yarn|bun)( run)? test|make test|gradle test|mvn test|dotnet test|ctest|rspec|"
                      r"phpunit)\b", re.IGNORECASE)


def is_test_command(command: str) -> bool:
    return bool(_TESTISH.search(command))


@dataclass
class Run:
    command: str
    exit_code: int | None
    seq: int
    at: str
    summary: str
    output: str = ""


@dataclass
class Evidence:
    """What really happened in a turn, in order. Filled from tool results, never from the model's text."""
    seq: int = 0
    runs: list[Run] = field(default_factory=list)
    last_change: int = -1            # seq of the last call that may have changed files
    tool_paths: list[str] = field(default_factory=list)
    shell_may_have_changed: bool = False

    def record(self, name: str, inp: dict, result: str, *, read_only_shell: bool = False) -> None:
        self.seq += 1
        failed = result.startswith(("Error", "Action denied", "User denied"))
        if name in ("write_file", "edit_file", "delete_files") and not failed:
            self.last_change = self.seq
            paths = inp.get("paths") if name == "delete_files" else [inp.get("file_path")]
            self.tool_paths += [str(p) for p in (paths or []) if p]
        elif name == "run_shell":
            command = inp.get("command", "")
            code = exit_code_of(result)
            self.runs.append(Run(command, code, self.seq, time.strftime("%H:%M"), summarize(result, code)))
            if not read_only_shell and not is_test_command(command) and code is not None:
                self.last_change = self.seq
                self.shell_may_have_changed = True

    def passing_after_change(self, commands: list[str] | None = None) -> Run | None:
        """The last successful run after the last change: of one of ``commands``, or any test-like one."""
        wanted = {_norm(c) for c in commands} if commands is not None else None
        for run in reversed(self.runs):
            if run.seq <= self.last_change:
                return None
            if run.exit_code == 0 and (wanted is None and is_test_command(run.command)
                                       or wanted is not None and _norm(run.command) in wanted):
                return run
        return None


def _norm(command: str) -> str:
    """Spelling-insensitive form: PowerShell's ``& "C:\\x\\python.exe" -m pytest`` is ``C:/x/python.exe -m pytest``."""
    c = command.strip()
    if c.startswith("& "):
        c = c[2:]
    c = c.replace('"', "").replace("'", "")
    return " ".join(c.replace("\\", "/").split()).lower()


def same_command(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


# ─── which files count ──────────────────────────────────────

_DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                 ".pdf", ".ico"}
_DOC_NAMES = {"license", "licence", "changelog", "authors", "contributors", "notice", ".gitignore"}


def is_doc(path: str) -> bool:
    p = Path(path.replace("\\", "/"))
    if p.suffix.lower() in _DOC_SUFFIXES or p.stem.lower() in _DOC_NAMES or p.name.lower() in _DOC_NAMES:
        return True
    return any(part.lower() in ("docs", "doc") for part in p.parts[:-1])


_TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|"
    r"(^|/)conftest\.py$|Tests?\.(java|kt|cs)$|_spec\.rb$",
    re.IGNORECASE,
)


def is_test_file(path: str) -> bool:
    return bool(_TEST_FILE.search(path.replace("\\", "/")))


_TEST_WORDS = re.compile(r"\b(tests?|testing|pytest|unittest|spec|specs|jest|vitest|assert\w*|coverage|fixtures?|"
                         r"xfail|skip)\b|测试|用例|单测|断言", re.IGNORECASE)


def mentions_tests(text: str) -> bool:
    """Is the task about tests? Then changing test files is expected, not suspicious."""
    return bool(_TEST_WORDS.search(text or ""))


# ─── weakened tests ─────────────────────────────────────────

_TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test\w*|\b(it|test)\s*\(\s*['\"`]|^\s*func\s+Test\w+|#\[test\]",
                       re.MULTILINE)
_SKIP = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|@unittest\.skip|\.skip\(|"
                   r"\b(xit|xdescribe|xtest)\s*\(|\.only\(|\bt\.Skip|#\[ignore\]|@Disabled|@Ignore")
_ASSERT = re.compile(r"\bassert\b|self\.assert\w*\(|\bexpect\s*\(|assert_eq!|assert_ne!|assert!\(|"
                     r"\bt\.(Error|Errorf|Fatal|Fatalf)\(|\.should\b|Assert\.\w+\(")
_TRIVIAL = re.compile(r"^\s*assert\s+(True|1|not\s+False)\s*(#.*)?$|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)",
                      re.MULTILINE)
_EXPECTED = (
    re.compile(r"^\s*assert\s+(.+?)\s*==\s*(.+?)\s*(#.*)?$"),
    re.compile(r"^\s*self\.assert(?:Equal|Equals)\(\s*(.+?),\s*(.+)\)\s*$"),
    re.compile(r"^\s*expect\((.+?)\)\.(?:toBe|toEqual|toStrictEqual)\((.+)\);?\s*$"),
    re.compile(r"^\s*assert_eq!\(\s*(.+?),\s*(.+)\);?\s*$"),
)


def _expectations(lines: list[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        for pattern in _EXPECTED:
            m = pattern.match(line)
            if m:
                found[m.group(1).strip()] = m.group(2).strip()
                break
    return found


def weakening(path: str, old: str | None, new: str | None) -> list[str]:
    """How a change to a test file makes the tests weaker. Empty: it does not (as far as can be told)."""
    if old is None:
        return []                                   # a new test file adds tests
    if new is None:
        n = len(_TEST_DEF.findall(old))
        return [f"{path}: test file deleted" + (f" ({n} tests)" if n else "")]
    found = []
    tests_before, tests_after = len(_TEST_DEF.findall(old)), len(_TEST_DEF.findall(new))
    if tests_after < tests_before:
        found.append(f"{path}: {tests_before - tests_after} test(s) removed")
    skips_before, skips_after = len(_SKIP.findall(old)), len(_SKIP.findall(new))
    if skips_after > skips_before:
        found.append(f"{path}: {skips_after - skips_before} skip/xfail/only marker(s) added")
    asserts_before, asserts_after = len(_ASSERT.findall(old)), len(_ASSERT.findall(new))
    if asserts_after < asserts_before and tests_after >= tests_before:
        found.append(f"{path}: {asserts_before - asserts_after} assertion(s) removed")
    if len(_TRIVIAL.findall(new)) > len(_TRIVIAL.findall(old)):
        found.append(f"{path}: an assertion that always passes was added")
    removed, added = [], []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("- "):
            removed.append(line[2:])
        elif line.startswith("+ "):
            added.append(line[2:])
    before, after = _expectations(removed), _expectations(added)
    for subject, value in before.items():
        if subject in after and after[subject] != value:
            found.append(f"{path}: expected value changed for `{subject}`: {value} → {after[subject]}")
    return found


# ─── claims ─────────────────────────────────────────────────

_CLAIM = re.compile(
    r"(all\s+(the\s+)?tests\s+(now\s+)?pass\w*|tests?\s+(are\s+|now\s+)*(passing|pass(ed)?|green)|"
    r"(build|lint|type[- ]?check)\w*\s+(now\s+)?(passes|passed|succeeds|succeeded|is clean)|"
    r"verified\s+(that\s+)?(it|the fix|everything)\s+works|everything\s+(now\s+)?works|"
    r"测试(已经|都|全部|均)?(通过|跑通|全过)|全部通过|验证(已)?通过|已验证)",
    re.IGNORECASE,
)


def claims(text: str) -> list[str]:
    """Sentences in the model's answer that claim checks pass."""
    found = []
    for sentence in re.split(r"(?<=[.!?。！？\n])\s*", text or ""):
        if _CLAIM.search(sentence) and not re.search(r"\b(not|n't|didn't|couldn't|unable|fail\w*)\b|未|没有|无法",
                                                       sentence, re.IGNORECASE):
            found.append(sentence.strip()[:160])
    return found


# A failure said to remain ("still fails", "also failing", "fails, but it is unrelated"), not one said fixed.
_ACK = re.compile(
    r"\b(still|also|remain\w*|continu\w*|already)\b[^.\n]{0,40}\b(fail\w*|broken|red)\b|"
    r"\bfail\w*\b[^.\n]{0,60}\b(unrelated|separate|pre-?existing|before (i|my)|outside)\b|"
    r"\b(unrelated|separate|pre-?existing)\b[^.\n]{0,60}\bfail\w*|"
    r"(仍|还|依然|仍然|依旧)[^。\n]{0,20}(失败|报错|没通过|未通过|不通过)|"
    r"(失败|报错|没通过|未通过)[^。\n]{0,30}(无关|之前就|原本|本来)",
    re.IGNORECASE,
)


def acknowledges_failure(text: str) -> bool:
    """The answer already says checks fail (and claims nothing passes): it is honest, so it is not pushed back."""
    return bool(_ACK.search(text or "")) and not claims(text)


# ─── the verdict ────────────────────────────────────────────

PASSED, FAILED, UNVERIFIED = "passed", "failed", "unverified"


@dataclass
class Outcome:
    status: str                      # passed | failed | unverified
    detail: str                      # one line: what ran, or why nothing did
    runs: list[Run] = field(default_factory=list)
    weakened: list[str] = field(default_factory=list)
    unproven: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)   # changed outside the request's scope (with an OK)
    warnings: list[str] = field(default_factory=list)       # e.g. a large change for a short request

    def event(self) -> dict:
        return {
            "status": self.status, "detail": self.detail, "weakened": self.weakened, "unproven": self.unproven,
            "changed": self.changed[:50], "out_of_scope": self.out_of_scope, "warnings": self.warnings,
            "runs": [{"command": r.command, "exit_code": r.exit_code, "summary": r.summary, "at": r.at}
                     for r in self.runs],
        }


def enabled() -> bool:
    return os.environ.get("KEELSON_VERIFY", "1").strip().lower() not in ("0", "false", "no", "off")


def timeout_s() -> float:
    try:
        return float(os.environ.get("KEELSON_VERIFY_TIMEOUT", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S


def retries() -> int:
    try:
        return max(0, int(os.environ.get("KEELSON_VERIFY_RETRIES", DEFAULT_RETRIES)))
    except ValueError:
        return DEFAULT_RETRIES
