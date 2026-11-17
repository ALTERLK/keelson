"""Web console for Keelson.

A thin layer over the runtime — it adds no agent behaviour of its own:

* ``server.py``   FastAPI app: an SSE stream of bus events, a few POST endpoints
                  for user actions, and read-only JSON views of repo artifacts.
* ``recorder.py`` a bus subscriber that writes a run to a JSONL trace, plus the
                  helpers that list and load traces for replay.
* ``data.py``     read-only views over benchmark runs and skill-evolution records.
* ``static/``     the front end (plain HTML/CSS/JS, no build step, no CDN).

Install with ``pip install -e ".[web]"`` and start with ``keelson web``.
"""
