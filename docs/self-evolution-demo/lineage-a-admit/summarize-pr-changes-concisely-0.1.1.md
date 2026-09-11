# Evolution Card — summarize-pr-changes-concisely @ 0.1.1

- **When:** 2026-09-11T05:31:06Z
- **Action:** `merge`
- **Gate:** `admit` — non-inferior on replay

## 1. Source conversation evidence

- **user**: 再总结一下这次的改动。
- **assistant**: - 重构了 payment 模块的错误处理 - 补充了边界条件的测试
- **user**: 每个要点最好用动词开头，比如'修复了'、'新增了'，这样读起来更清楚。

> Extractor evidence quote: 每个要点最好用动词开头，比如'修复了'、'新增了'，这样读起来更清楚。

## 2. Extractor candidate

- **name:** format-change-summary-with-action-verbs
- **when_to_use:** Use when summarizing code changes, pull requests, release notes, or updates in Chinese where bullet points should be structured and easy to scan.
- **description:** Format bullet points in change summaries or changelogs starting with clear action verbs (e.g., '修复了', '新增了', '优化了').

```
1. Begin each bullet point with an explicit action verb indicating the nature of the change (e.g., '修复了' for bug fixes, '新增了' for new features, '优化了' for performance/refactoring, '更新了' for updates).
2. Keep each point concise, clearly stating the component or scope followed by the specific change made.
3. Ensure consistent grammatical structure across all bullet items.
```

## 3. Maintainer decision

- **verdict:** merge
- **rationale:** The candidate refines how bullet points in PR change summaries should be structured by specifying action verbs, which directly enhances the existing PR change summarization skill.
- **similar skills considered:**
  - _(none retrieved)_

## 4. SKILL.md change

```diff
--- summarize-pr-changes-concisely@0.1.0 (2026-09-11T05:31:06Z)
+++ summarize-pr-changes-concisely@current
@@ -1,20 +1,23 @@
 ---
 name: summarize-pr-changes-concisely
-description: Format pull request (PR) change summaries as concise bullet points, avoiding lengthy paragraphs.
-version: 0.1.0
+description: Format pull request (PR) change summaries and changelogs as concise bullet points starting with clear action verbs.
+version: 0.1.1
 created-at: 2026-09-11T05:30:58Z
 user-invocable: false
 context: inline
-when-to-use: When the user asks to summarize PR changes, code updates, or commit diffs.
-tags: git,pull-request,summary,formatting
+when-to-use: When summarizing PR changes, code updates, commit diffs, or changelogs, especially when concise and structured bullet points are needed.
+tags: git,pull-request,summary,formatting,changelog,pr-summary,writing-style
+last-evolved: 2026-09-11T05:31:06Z
+evolution-count: 1
 ---
 
 # Skill Instructions
 
-- Present the PR changes as a concise bulleted list.
-- Keep each bullet point brief, clear, and focused on key modifications.
-- Avoid large blocks of text or verbose explanations.
+- Present changes as a concise bulleted list; avoid large blocks of text or verbose explanations.
+- Begin each bullet point with an explicit action verb indicating the nature of the change (e.g., '修复了' for bug fixes, '新增了' for new features, '优化了' for performance/refactoring, '更新了' for general updates).
+- Clearly state the affected component or scope followed by the specific change made.
+- Maintain consistent grammatical structure and brief, scannable phrasing across all bullet items.
 
-## Creation Evidence
+## Evolution Notes
 
-- 以后总结 PR 改动都用要点列表的形式，保持简洁，不要写大段文字。
+- 2026-09-11: 每个要点最好用动词开头，比如'修复了'、'新增了'，这样读起来更清楚。 Reason: The candidate refines how bullet points in PR change summaries should be structured by specifying action verbs, which directly enhances the existing PR change summarization skill.
```

## 5. Non-inferiority gate

```json
{
  "action": "admit",
  "reason": "non-inferior on replay",
  "skill": "summarize-pr-changes-concisely",
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
    "hard_failures": 0,
    "pass_rate": 1.0,
    "rule_count": 1
  }
}
```

## 6. Result → **summarize-pr-changes-concisely @ 0.1.1**

- file: `<scratch-keelson-home>\.keelson\skills\summarize-pr-changes-concisely\SKILL.md`
- ok: True
