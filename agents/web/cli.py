"""``keelson web`` — start the web console."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="keelson web", description="Keelson web console")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (loopback only by default)")
    parser.add_argument("--in-container", action="store_true",
                        help="Allow a non-loopback --host; only for containers that publish the port to loopback")
    parser.add_argument("--replay", action="store_true", help="Serve recorded traces only; no model is called")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab")
    parser.add_argument("--no-record", action="store_true", help="Do not record this session to .keelson/traces/")
    parser.add_argument("--accept-edits", action="store_true", help="Start in acceptEdits mode")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    return parser.parse_args(argv)


def _build_agent(args: argparse.Namespace):
    """An Agent configured exactly as the REPL would configure it, or None."""
    from ..agent import Agent
    from ..main import _load_env_file, _resolve_api_config

    _load_env_file()
    api_base, api_key, use_openai = _resolve_api_config(args.api_base)
    if not api_key:
        return None
    return Agent(
        permission_mode="acceptEdits" if args.accept_edits else "default",
        model=args.model or os.environ.get("MODEL") or "deepseek-chat",
        max_turns=args.max_turns,
        api_base=api_base if use_openai else None,
        anthropic_base_url=api_base if not use_openai else None,
        api_key=api_key,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[2:] if argv is None else argv)

    try:
        import uvicorn

        from .server import Session, create_app
    except ImportError:
        print('The web console needs extra packages. Install them with:\n\n    pip install -e ".[web]"\n')
        return 1

    from .. import paths, ui
    from .recorder import TraceRecorder

    if args.host not in _LOOPBACK and not args.in_container:
        print(
            f"Refusing to bind {args.host}: the console can run shell commands and has no\n"
            "authentication, so it only listens on loopback. Use an SSH tunnel for remote access."
        )
        return 2

    agent = None if args.replay else _build_agent(args)
    if agent is None and not args.replay:
        print("No API key found (.env / APIKEY / OPENAI_API_KEY / ANTHROPIC_API_KEY) — starting in replay mode.")

    # The browser is the display now; keep stdout for the server's own log.
    ui.set_terminal_enabled(False)

    recorder = None
    if agent is not None and not args.no_record:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        recorder = TraceRecorder(
            paths.project_dir() / "traces" / f"{stamp}-{agent.session_id}.jsonl",
            meta={"model": agent.model},
        )

    session = Session(agent, recorder=recorder)
    url = f"http://{args.host}:{args.port}/"
    print(f"\n  Keelson console  {url}   ({'live: ' + agent.model if agent else 'replay mode'})")
    print("  Ctrl+C to stop\n")

    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    try:
        uvicorn.run(
            create_app(session),
            host=args.host,
            port=args.port,
            log_level="warning",
            timeout_graceful_shutdown=2,  # open SSE streams would otherwise hold shutdown
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
