# Proposal C4: models to compare, and a spending cap

Status: proposal for the owner. Nothing has been called; no money has been spent.
Prices are the list prices per million tokens as of 2026-11 (input / output).

## Models

| Model | Model id | Price in / out | Why it is on the list |
|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | $4 / $20 | Default choice: the model the LLM backend is built and prompted for. |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | Half the price. Shows whether the expensive model earns its cost. |
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 | Forty times cheaper than Opus. The floor: if it matches, the case set is too easy. |
| Claude Fable 5.2 (optional) | `claude-fable-5-2` | $8 / $40 | The most capable model. Shows the ceiling. Twice Opus's price, so optional. |

One provider only. The project's rules allow any model available through Claude
Code; the comparison is kept to one provider so that differences come from the
models and not from different SDKs, prompts or tool formats.

## What the comparison runs

Following the approved design: evidence is recorded once per case, then replayed
to each model. Each model sees identical input.

| Item | Count |
|---|---|
| Cases | 18 existing + up to 6 holdout = 24 |
| Repeats per case in replay (stability) | 5 |
| Calls per model per pass | 24 × 5 = 120 diagnoses |
| Model calls per diagnosis | up to 3 (orchestrator rounds) |

## Cost estimate

Assumed per model call: about 4,000 input tokens (alert, fenced evidence,
instructions) and about 2,000 output tokens (reasoning plus the diagnosis).
These are estimates; the first ten calls will be measured and the estimate
corrected before the rest run.

| Model | Per call | Per pass, 1 call per diagnosis | Per pass, worst case 3 calls |
|---|---|---|---|
| Opus 5.5 | about $0.056 | about $6.7 | about $20 |
| Sonnet 5.5 | about $0.028 | about $3.4 | about $10 |
| Haiku 5.5 | about $0.0014 | about $0.17 | about $0.50 |
| Fable 5.2 | about $0.112 | about $13.4 | about $40 |

Plus the end-to-end layer with one model (24 cases, 1 run each): under $5.

## Proposed cap

| Option | Models | Cap |
|---|---|---|
| A (recommended) | Opus 5.5, Sonnet 5.5, Haiku 5.5 | **$40** |
| B | A plus Fable 5.2 | $90 |

How the cap is enforced: the runner adds up the actual cost from each
response's token counts, prints the running total, and stops before a call
that would cross the cap. A partial report is kept.

## Needed from the owner before any paid run

1. Choose option A or B, or name other models.
2. Confirm the cap.
3. An API key. It goes only into `.env`, which git ignores. Without a key the
   system keeps running on the rule engine, as designed.
