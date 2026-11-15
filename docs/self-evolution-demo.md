# Self-evolution, live-fired: one real promotion, one real quarantine

Every skill in this project's actual usage history has `promoted: true` exactly
**zero** times (`grep -r '"promoted": true' .keelson/` — nothing). Not because the
gate doesn't work — `agents/skill_gate.py` has 60 lines of unit tests — but
because it had never been driven end to end with real replay data behind it.
This page is that run: two independent skill lineages, driven through the
**real** `online_ingest()` → Extractor → Maintainer → `evolve_skill(gate=True)`
pipeline with a real model, twice each — one lands on a real `admit`, one on a
real `quarantine`. Script: `scripts/demo_self_evolution.py`. Artifacts:
`docs/self-evolution-demo/lineage-a-admit/` and `lineage-b-quarantine/`.

Both lineages ran **naturally** — the Maintainer's own merge decision landed on
the intended gate outcome without needing the script's deterministic fallback
path (which exists for exactly this case and is proven separately in
`tests/test_skill_gate.py`'s end-to-end tests, no API key required).

## Why the gate had never fired for real

`gate_evolution()` needs at least one **replay sample** — a recorded past
reply for the skill — to compare old vs. new instructions against. A skill's
*first* evolution has no replay sample yet, so the gate returns `ungated`
(admitted, unverified) by design. Only a skill's **second** evolution, once
one round of usage has left a provenance record, can actually be scored. That
second round is what this demo drives.

## Lineage A — a real `admit`

**Round 1** (`lineage-a-admit/run_log.json`, step `round1 online_ingest`): a
user asks for a PR summary, gets a bulleted reply, then gives feedback —
*"以后总结 PR 改动都用要点列表的形式，保持简洁，不要写大段文字"* ("format PR
summaries as bullet points from now on, keep it concise"). The Extractor pulls
a candidate from that feedback; the Maintainer creates skill
`summarize-pr-changes-concisely` (`action: "add"`) — v0.1.0
(`lineage-a-admit/history-00-v0.1.0.md`). This write also seeds the replay
sample the gate will use next: `lineage-a-admit/provenance.json`.

**Round 2**: a related but non-conflicting correction — *"每个要点最好用动词开头"*
("start each bullet with a verb"). The Maintainer retrieves the existing skill
as similar and decides `merge`. `evolve_skill(gate=True)` runs the real gate:

| | old (v0.1.0) | new (v0.1.1 candidate) |
|---|---:|---:|
| hard_failures | 0 | 0 |
| avg_score | 2.0 | 2.0 |

No new hard rule was introduced by the merge (the added instruction — "start
with a verb" — doesn't touch any of `_compile_eval_rules`'s trigger phrases),
so the round-1 replay sample still satisfies everything → **`admit`**. Live
file updated to v0.1.1 (`lineage-a-admit/SKILL.md`), Evolution Note recording
the exact feedback that caused it.

## Lineage B — a real `quarantine`

**Round 1** (isolated `KEELSON_HOME`/cwd, a separate skill so it can't be
merged into lineage A's — see the note in the script): a release-notes
request, a bulleted reply that never cites a source, then feedback —
*"以后写 release notes 用要点列表，保持简洁"*. Skill `format_release_notes`
created, v0.1.0, replay sample seeded.

**Round 2**: *"以后每一条 release notes 都要标注来源 issue/PR 链接，方便追溯，
必须注明来源"* ("every release note must now cite its source issue/PR link").
The Maintainer merges this in — reasonably, it's a real refinement — producing
a candidate that requires source links
(`lineage-b-quarantine/format_release_notes-20260911T053122Z.md`). The gate:

| | old (v0.1.0) | new (candidate v0.1.1) |
|---|---:|---:|
| hard_failures | 0 | **1** |
| avg_score | 2.0 | 2.0 |
| rule_count | 1 | 2 |

The candidate's own text trips `must_cite_sources` — a hard, **programmatic**
rule that `_compile_eval_rules` compiles whenever a literal trigger phrase
("标注来源" / "注明来源" / "cite sources" / …) appears anywhere in the skill's
text (frontmatter description, when-to-use, instructions, or — as here — the
auto-appended Evolution Note quoting the user's own feedback verbatim).
Scored against that new rule, the round-1 replay reply (which never cited a
source, because that requirement didn't exist yet) fails it → **`quarantine`**.
`skills.py::_apply_evolution_gate` then:

1. writes the rejected candidate to `quarantine/` (copied here as
   `format_release_notes-20260911T053122Z.md` + its decision JSON),
2. rolls the live file back (`rollback()` — itself a tracked, reversible
   event: the live `SKILL.md` shows `rolled-back-from: 0.1.1` /
   `rolled-back-to: 0.1.0` in its frontmatter, not silently reverted),
3. writes an Evolution Card titled **`— REJECTED`**
   (`format_release_notes-rejected-20260911T053122Z.md`) — this round also
   closed a real gap found while building this demo: quarantined candidates
   previously got *no* card at all (`agents/evolution_card.py`,
   `agents/online_skill_evolution.py`); now a rejection is exactly as
   auditable as an acceptance.

Live file: `lineage-b-quarantine/SKILL.md` — content matches the pre-candidate
version; the citation requirement never reached it.

## What this answers

The objections a self-modifying skill system has to meet, and where each is answered:

| objection | where the answer lives |
|---|---|
| If a user's follow-up is what triggers a rewrite, is that really *self*-evolution? | Both lineages: extraction keys only on a **user feedback turn** (`extract_online_skill_candidate`'s own rules — "USER turns are the primary evidence"), never on the model grading its own output. |
| With no human review step, what keeps evolution in check? | The gate is the review loop, now shown firing both ways: `docs/self-evolution-demo/lineage-a-admit/` (passed) and `lineage-b-quarantine/` (rejected + rolled back), each with a full Evolution Card. |
| What exactly happens between one exchange and a changed skill? | Both `run_log.json` files are the literal trace: conversation → Extractor candidate → Maintainer decision → gate (old/new scores) → result. |
| If a skill can be rewritten after any exchange, how is behaviour kept stable? | It can be rewritten, but not *land* unverified once a second round exists — lineage B shows a real rewrite attempt rejected and the live file unchanged. |

## Known limits of this evidence

- **n=1 replay sample per lineage.** The gate's non-inferiority check is
  proven correct on 1 historical sample per skill, not a large replay pool —
  that's the honest minimum the mechanism needs to stop being `ungated`, not a
  claim about statistical power. `tests/test_skill_gate.py` covers the same
  mechanism deterministically without depending on how many samples happen to
  exist.
- **Rule coverage is what a skill's own text declares.** Both gate decisions
  here ride on `_compile_eval_rules`'s keyword-triggered rules
  (`must_cite_sources`, in lineage B's case) — the same mechanism, not a
  special case built for this demo. A skill whose text never mentions a
  gateable pattern (citations, JSON output, a paragraph limit, …) only ever
  gets the baseline `response_nonempty` check.
- Paths inside the copied Evolution Cards (e.g. `rejected candidate saved
  at: C:\Users\...\Temp\...`) point at the run's scratch `KEELSON_HOME`, which
  is deleted after the script exits — the actual rejected file is copied
  alongside the card in the same folder, not lost, just at a different path
  than the card text says.
