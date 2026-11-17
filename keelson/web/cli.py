"""``keelson web`` — start the web console."""

from __future__ import annotations

import argparse
import os
import sys
import threading
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


def _agent_factory(args: argparse.Namespace):
    """A function that builds an Agent as the REPL would, or None with no key.

    A factory rather than one agent: every web session gets its own.
    """
    from ..agent import Agent
    from ..main import _load_env_file, _resolve_api_config

    _load_env_file()
    api_base, api_key, use_openai = _resolve_api_config(args.api_base)
    if not api_key:
        return None, ""
    model = args.model or os.environ.get("MODEL") or "deepseek-chat"

    def build() -> Agent:
        return Agent(
            permission_mode="acceptEdits" if args.accept_edits else "default",
            model=model,
            max_turns=args.max_turns,
            api_base=api_base if use_openai else None,
            anthropic_base_url=api_base if not use_openai else None,
            api_key=api_key,
        )

    return build, model


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[2:] if argv is None else argv)

    try:
        import uvicorn

        from .server import SessionManager, create_app
    except ImportError:
        print('The web console needs extra packages. Install them with:\n\n    pip install -e ".[web]"\n')
        return 1

    from .. import paths, ui

    if args.host not in _LOOPBACK and not args.in_container:
        print(
            f"Refusing to bind {args.host}: the console can run shell commands and has no\n"
            "authentication, so it only listens on loopback. Use an SSH tunnel for remote access."
        )
        return 2

    factory, model = (None, "") if args.replay else _agent_factory(args)
    if factory is None and not args.replay:
        print("No model is configured — starting in replay mode. Run `keelson init` to set one up.")

    # The browser is the display now; keep stdout for the server's own log.
    ui.set_terminal_enabled(False)

    manager = SessionManager(
        factory,
        traces_dir=paths.project_dir() / "traces",
        record=not args.no_record,
    )
    url = f"http://{'localhost' if args.in_container else args.host}:{args.port}/"
    print(f"\n  Keelson console  {url}   ({'live: ' + model if factory else 'replay mode'})")
    print("  Ctrl+C to stop\n")

    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    try:
        uvicorn.run(
            create_app(manager),
            host=args.host,
            port=args.port,
            log_level="warning",
            timeout_graceful_shutdown=2,  # open SSE streams would otherwise hold shutdown
        )
    finally:
        manager.close_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
