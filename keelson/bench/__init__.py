"""Offline benchmark harness for Keelson.

Currently ships one dataset adapter — ToolHop (multi-hop tool calling, every
task carries executable Python tool bodies plus a gold answer, so scoring is
deterministic and fully offline).
"""
