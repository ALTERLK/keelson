---
name: summarize-pr-changes-concisely
description: Format pull request (PR) change summaries and changelogs as concise bullet points starting with clear action verbs.
version: 0.1.1
created-at: 2026-09-11T05:30:58Z
user-invocable: false
context: inline
when-to-use: When summarizing PR changes, code updates, commit diffs, or changelogs, especially when concise and structured bullet points are needed.
tags: git,pull-request,summary,formatting,changelog,pr-summary,writing-style
last-evolved: 2026-09-11T05:31:06Z
evolution-count: 1
---

# Skill Instructions

- Present changes as a concise bulleted list; avoid large blocks of text or verbose explanations.
- Begin each bullet point with an explicit action verb indicating the nature of the change (e.g., '修复了' for bug fixes, '新增了' for new features, '优化了' for performance/refactoring, '更新了' for general updates).
- Clearly state the affected component or scope followed by the specific change made.
- Maintain consistent grammatical structure and brief, scannable phrasing across all bullet items.

## Evolution Notes

- 2026-09-11: 每个要点最好用动词开头，比如'修复了'、'新增了'，这样读起来更清楚。 Reason: The candidate refines how bullet points in PR change summaries should be structured by specifying action verbs, which directly enhances the existing PR change summarization skill.
