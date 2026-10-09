---
name: jev
description: Use Jev (TypeSafe's typesafe/jev-1.13, called through OpenRouter) to sort, classify, score, triage, route, or filter any pile of text — emails, leads, tickets, comments, documents, rows. Trigger on "use Jev", "sort these", "classify these", "triage", "which of these…", "score these", "is this X or Y" over a batch of text. Jev decides; Claude writes. Not for generating text.
---

# Jev — fast, cheap, typed decisions

Jev is a "System One" model: you hand it some text (the **state**) plus typed **questions**, and it
returns calibrated, structured answers in well under a second, for a fraction of a cent.
It never writes prose. Docs: https://docs.typesafe.ai/llms.txt

**Also load the `typesafe-ai` skill** (TypeSafe's official guidance) when designing questions or any
TypeSafe integration for this project; this `jev` skill adds the OpenRouter call, the user's rules,
and lessons from real runs.

## The house rules (from the user)

1. **Jev decides, Claude writes.** Use Jev for every sorting/deciding judgment; Claude writes any
   summaries, replies, or reports from Jev's answers.
2. **When Jev isn't sure, Claude makes the call** — and says it did (see thresholds below).
3. **Anything sent to Jev leaves the user's computer.** Before sending anything private (personal
   emails, client data, names + contact details, financials, health, credentials, internal documents),
   **ask the user first**. Made-up or already-public text is fine to send. Never send secrets/keys.

## How to call it

Write a request file `{"state": ..., "questions": {...}}` and run:

```bash
python3 .claude/skills/jev/scripts/jev.py request.json            # send it
python3 .claude/skills/jev/scripts/jev.py request.json --dry-run  # show exactly what would be sent
```

- Endpoint: `POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`
  (OpenRouter's Decisions route; if it moves, re-check https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).
- Key: env var `OPENROUTER_API_KEY`, else `~/.config/openrouter/key` (chmod 600), else none — in a
  Claude Code cloud environment a **network secret** for `openrouter.ai` adds it in transit (preferred). Never print,
  echo, commit, or paste the key anywhere; never ask the user to paste it into chat.
- The script prints `answers`, `model`, `usage.cost` (USD), and `elapsed_ms`. On failure it prints
  the exact HTTP status + body or network error — show that to the user verbatim.
- Verified 2026-10-09 in the cloud "Default" environment (network secret for `openrouter.ai`):
  3 questions on one email → 701 ms round trip, $0.0000316 (752 input tokens).
- Worked example: `examples/sales_email_test.json` (lead strength / email type / needs personal reply).

## The three question shapes (the only ones Jev has)

| Type | Use for | `criteria` | Answer |
|---|---|---|---|
| `choice` | pick one option from an unordered list | object `{option_id: description}` — add an `other` option when the list may not cover everything | `choice`, `probabilities`, `confidence` |
| `score` | a position on an ordered scale | array of level descriptions, low → high, 2–10 levels | `score` (0…n-1, can be fractional), `legend`, `probabilities`, `confidence` |
| `noul` | how likely a yes/no statement is true | optional `{"true": ..., "false": ...}` | `noul` (0–1 probability of yes); **no** `confidence` |

Every question needs `type` and `instructions`. Question IDs are for code only — the model never
sees them, so put the whole question in `instructions`.

## Writing good questions

- **One snap judgment per question.** Split complex calls ("is this a good lead?") into several
  atomic questions and combine them in code with weights.
- **Batch everything** for the same state into one request — questions run in parallel, extra ones
  are nearly free. Ask speculative questions and ignore the irrelevant answers.
- **Many items → one request per item.** Learned on a 74-email inbox (2026-10-09): packing all items into
  one state and asking ``about `emails[i]` `` degraded badly after ~20 items (confidence collapsed, wrong
  picks). One small request per item, same questions, run ~10 in parallel: 74 emails in 4.7 s wall,
  median 0.37 s per call, ~$0.00003 per email, with clean answers. Keep the batched-list form only for
  short lists (<15 items).
- **Point at fields** in a JSON state with backticked paths: ``Does `ticket.messages[0].text` request a refund?``
- **Score levels describe situations, not degrees** ("Broken, but a workaround exists", not
  "moderately severe"). No numbers-only levels. One dimension per Score.
- **Noul phrasing:** high = yes. No negated/inverted questions, one condition per Noul.
- **Be literal and exact** — Jev answers the words written, not the intent.

## Jev's known weak spots (jev-1.13) — keep these in code or with Claude

Counting, arithmetic, numeric precision, date comparisons/ordering, multi-hop/indirect reasoning,
huge states full of irrelevant text (filter first; limit 32k tokens for state + longest question,
64k per request), adversarial/injected content, generating text. Choice can lean toward the first
option — for important calls, reorder options and re-ask to check consistency. English is strongest.

## Confidence → who decides

Default thresholds (tighten for high-stakes actions):

- **Choice / Score:** `confidence ≥ 0.6` → accept Jev's answer. `0.3–0.6` → accept but flag it.
  `< 0.3` → Claude decides by reading the text, and says so.
- **Noul:** `≥ 0.8` yes, `≤ 0.2` no; in between → Claude decides.
- Score: also read `probabilities` — a split between neighbouring levels is milder than a split
  between ends. Round `score` to the nearest level only when a single bucket is needed.

## Reporting results to the user

Present a compact table: item → each answer (with confidence / probability), then mark any item
where Claude overrode or decided because Jev was unsure. Include total `usage.cost` and time taken.
Then Claude writes whatever prose the user asked for (replies, summaries) from those decisions.
