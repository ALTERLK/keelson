# Evolution Card — summarize-pr-changes-concisely @ 0

- **When:** 2026-09-11T05:30:58Z
- **Action:** `add`
- **Gate:** `n/a` — —

## 1. Source conversation evidence

- **user**: 帮我总结一下这次 PR 的改动。
- **assistant**: - 修改了 auth.py 里的登录校验逻辑 - 新增了对应的单元测试 - 更新了 README 里的接口说明
- **user**: 以后总结 PR 改动都用要点列表的形式，保持简洁，不要写大段文字。

> Extractor evidence quote: - 以后总结 PR 改动都用要点列表的形式，保持简洁，不要写大段文字。

## 2. Extractor candidate

- **name:** summarize-pr-changes-concisely
- **when_to_use:** When the user asks to summarize PR changes, code updates, or commit diffs.
- **description:** Format pull request (PR) change summaries as concise bullet points, avoiding lengthy paragraphs.

```
- Present the PR changes as a concise bulleted list.
- Keep each bullet point brief, clear, and focused on key modifications.
- Avoid large blocks of text or verbose explanations.
```

## 3. Maintainer decision

- **verdict:** add
- **rationale:** No existing skills match this durable preference for concise PR summary formatting.
- **similar skills considered:**
  - _(none retrieved)_

## 4. SKILL.md change

```diff
(new skill — no prior version)
```

## 5. Non-inferiority gate

```json
{
  "action": "n/a"
}
```

## 6. Result → **summarize-pr-changes-concisely @ 0**

- file: `<scratch-keelson-home>\.keelson\skills\summarize-pr-changes-concisely\SKILL.md`
- ok: True
