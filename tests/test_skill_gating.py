"""Cheap feedback pre-classifier for the evolution pipeline."""

from __future__ import annotations

import pytest

from agents.skill_gating import gating_reason, looks_like_feedback


@pytest.mark.parametrize("msg", [
    "以后写政府报告都要更书面化并结合政策",
    "不要再连续追问了，先给初稿",
    "this is too verbose — keep it short next time",
    "你漏了调用方参数的检查",
    "from now on, lead with the conclusion",
    "记住：日期一律用 ISO 格式",
])
def test_positive_feedback(msg):
    assert looks_like_feedback(msg) is True


@pytest.mark.parametrize("msg", [
    "帮我写一个登录接口",
    "write a binary search in python",
    "2 + 2 等于几",
    "ok thanks",
    "生成一段测试数据",
    "",
    "hi",
])
def test_negative_not_feedback(msg):
    assert looks_like_feedback(msg) is False


def test_new_task_prefix_with_embedded_rule_still_counts():
    # a "write a ..." that also carries a durable rule should not be skipped
    assert looks_like_feedback("write a report, and from now on always cite sources") is True


def test_gating_reason_mentions_trigger():
    assert "以后" in gating_reason("以后都要这样做")
    assert "no feedback" in gating_reason("帮我查个天气")
