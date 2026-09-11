# Evolution Card — format_release_notes @ rejected-20260911T053122Z — REJECTED

- **When:** 2026-09-11T05:31:22Z
- **Action:** `merge_quarantined`
- **Gate:** `quarantine` — hard_failures 1 > 0

## 1. Source conversation evidence

- **user**: 这次的 release notes 呢？
- **assistant**: - 修复了登录超时的问题 - 新增了深色模式
- **user**: 以后每一条 release notes 都要标注来源 issue/PR 链接，方便追溯，必须注明来源。

> Extractor evidence quote: 以后每一条 release notes 都要标注来源 issue/PR 链接，方便追溯，必须注明来源。

## 2. Extractor candidate

- **name:** include-issue-pr-links-in-release-notes
- **when_to_use:** Use when drafting, generating, or formatting release notes, changelogs, or update summaries.
- **description:** Ensure every release note entry explicitly references its source issue or pull request link for traceability.

```
1. When writing release notes or changelog entries, do not list changes in isolation.
2. For each bullet point or entry, include the corresponding issue number, pull request reference, or direct link.
3. Ensure the source reference is clearly visible and facilitates traceability back to the original task or discussion.
```

## 3. Maintainer decision

- **verdict:** merge
- **rationale:** Both skills define release note formatting standards; merging links issue/PR traceability directly into the release note bullet point guidelines.
- **similar skills considered:**
  - _(none retrieved)_

## 4. SKILL.md change

```diff
(candidate rejected by the gate before it reached the live file — see the quarantine file below for the rejected text)
```

## 5. Non-inferiority gate

```json
{
  "action": "quarantine",
  "reason": "hard_failures 1 > 0",
  "skill": "format_release_notes",
  "samples": 1,
  "delta_score": 0.0,
  "old": {
    "n": 1,
    "avg_score": 2.0,
    "hard_failures": 0,
    "pass_rate": 1.0,
    "rule_count": 1
  },
  "new": {
    "n": 1,
    "avg_score": 2.0,
    "hard_failures": 1,
    "pass_rate": 0.5,
    "rule_count": 2
  }
}
```

## 6. Result → **REJECTED, live file unchanged**

- reason: candidate regressed on replay; rolled back
- rejected candidate saved at: `<scratch-keelson-home>\.keelson\skill-evolution\quarantine\format_release_notes-20260911T053122Z.md`
- the live SKILL.md was rolled back to its pre-candidate version
