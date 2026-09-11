---
name: multihop-tool-chaining
description: Answer multi-hop questions by chaining tool calls, feeding each result into the next.
when-to-use: The question needs several dependent lookups or computations and a set of tools is provided.
user-invocable: "false"
context: inline
version: 1.0.0
---

# Multi-hop tool chaining

The question decomposes into an ordered chain of sub-questions. Each tool answers
one hop; the answer to hop *k* is an input to hop *k+1*.

## Workflow

1. **Plan the chain first.** Name each hop and which tool answers it before calling anything.
2. **One hop per call.** Call the tool for hop 1, read its result, then use that exact
   value as the argument for hop 2. Do not guess a hop's output — always call the tool.
3. **Carry values verbatim.** Copy names, dates and numbers from a tool result into the
   next call exactly as returned; do not paraphrase or round.
4. **Check the type.** If a hop returns an object, pull the specific field the next hop
   needs. If it returns an error string, fix the arguments and retry that hop once.
5. **Final answer only.** After the last hop, output just the answer on the last line,
   prefixed `ANSWER: ` — no restating the chain.

## Common failures to avoid

- Skipping a hop and inventing its result.
- Passing the whole previous tool result as one argument instead of the needed field.
- Stopping after an intermediate hop and answering with a sub-answer.
- Reformatting a date/number between hops so the next tool can't match it.
