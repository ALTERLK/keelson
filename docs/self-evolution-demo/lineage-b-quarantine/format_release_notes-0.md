# Evolution Card — format_release_notes @ 0

- **When:** 2026-09-11T05:31:12Z
- **Action:** `add`
- **Gate:** `n/a` — —

## 1. Source conversation evidence

- **user**: 帮我写一下这次发布的 release notes。
- **assistant**: - 修复了分页边界的 bug - 新增了导出 CSV 的功能 - 优化了首页加载速度
- **user**: 以后写 release notes 用要点列表，保持简洁。

> Extractor evidence quote: 以后写 release notes 用要点列表，保持简洁。

## 2. Extractor candidate

- **name:** format_release_notes
- **when_to_use:** When generating or formatting release notes, changelogs, or update summaries.
- **description:** Standard for writing release notes using concise bullet points.

```
Present the release notes as a concise bulleted list without unnecessary elaboration.
```

## 3. Maintainer decision

- **verdict:** add
- **rationale:** No existing skill matches this formatting standard, adding as a new durable user preference skill.
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

## 6. Result → **format_release_notes @ 0**

- file: `<scratch-keelson-home>\.keelson\skills\format_release_notes\SKILL.md`
- ok: True
