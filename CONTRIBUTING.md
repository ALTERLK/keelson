# Contributing

Thanks for taking the time. Issues and pull requests are welcome, in English or
Chinese. 中文 issue 和 PR 同样欢迎。

## Setting up

```bash
git clone https://github.com/ALTERLK/keelson.git
cd keelson
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -e ".[dev,bench,web]"
```

## Before you open a pull request

```bash
ruff check .                              # lint
pytest -q                                 # all tests; none needs an API key
python scripts/build_pages.py             # the static demo still builds
python scripts/build_diagrams.py --check  # figures match the script
```

CI runs the same four on Python 3.11 and 3.12, plus a Docker build.

## What makes a change easy to merge

- **One thing per pull request.** A bug fix and a refactor are two pull requests.
- **A test that fails without the change.** For a bug, start with the test.
  Model calls are mocked in the test-suite; see `tests/test_web_server.py`
  (`StubAgent`) and `tests/test_openai_tool_loop.py` for the patterns.
- **Say why in the description**, not only what. If a number changes, say how
  it was measured.
- **Keep the model-facing strings stable** unless the change is about them.
  Tool result formats (`Command failed (exit code N)…`) are part of what the
  benchmarks measured.

## Where things live

| To change… | Look in |
|---|---|
| the loop, tool dispatch, the loop guard | `keelson/agent.py` |
| a tool or the permission gate | `keelson/tools.py` |
| what the runtime reports (events, spans) | `keelson/events.py`, then the subscribers: `keelson/ui.py`, `keelson/web/static/js/store.js` |
| the web console | `keelson/web/` (server, sessions) and `keelson/web/static/` (no build step: edit and reload) |
| a figure in the docs | `scripts/build_diagrams.py`, then re-run it |
| the demo traces | `scripts/record_demo.py` (needs an API key) |

The interface is bilingual: a new string goes into both dictionaries in
`keelson/web/static/js/i18n.js`, and a new doc page should exist under both
`docs/` and `docs/zh/`.

## Recorded traces

Traces under `demo/traces/` are published. `scripts/record_demo.py` rewrites
local paths and refuses to save a trace that still contains a user name, an
absolute Windows path or something shaped like an API key. Please do not commit
a trace recorded any other way.

## Feature experiments

`bench/FEATURES.md` is generated from the feature experiments added in 0.4:
one suite per signature feature (`verify`, `scope`, `ledger`, `undo`,
`danger`), checking that the feature does what it claims with a real model.

```bash
python -m keelson.bench.features --validate           # fixtures only; no API key
python -m keelson.bench.features verify --budget-usd 2   # a real run; needs a key
python scripts/features_report.py --check            # tables match the raw results
python scripts/dogfood_report.py                     # the table in docs/dogfooding.md
```

Change a task under `keelson/bench/feature_tasks.py` and run `--validate`
before anything else: without calling a model, it checks that every starter
fails its check and every reference solution passes, so a broken task fails
there in about half a minute instead of after a paid run.

## Reporting a bug

Include the output of `keelson doctor`, the command you ran, and what you
expected. If it happened in the web console, the session's trace
(`.keelson/traces/…jsonl`) is the most useful attachment — check it for
anything private first.

Security problems: see [SECURITY.md](SECURITY.md) rather than opening a public
issue.
