<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Prompt anatomy: what fills the input

**Method — ablation, not estimation.** A real 530-token prompt (pulled from a
logged `ok` call) was sent to the API repeatedly with one block removed each time,
reading `usage.prompt_tokens` back. Each block's cost is the drop it causes. No
tokenizer was used and none was installed; the API's own count is ground truth.

Why this matters: `tiktoken` was deliberately not added as a dependency (AGENTS.md
§8 — no new dependency where local work will do).

## Breakdown of a 530-token prompt

| Block | Tokens | Share | Varies per move? |
|---|---|---|---|
| System prompt — tone rules + JSON schema | 237 | 44.7% | no |
| Chat template & role scaffolding | 117 | 22.1% | no |
| User instruction block | 51 | 9.6% | no |
| Board position (FEN) | 50 | 9.4% | yes |
| Candidate move list (15 engine moves) | 47 | 8.9% | yes |
| Rolling tone summary | 24 | 4.5% | yes, grows |
| **The player's message** | **4** | **0.8%** | yes |

Sums to exactly 100.0%.

## The finding

**405 of 530 tokens (76.4%) are byte-identical on every single move.** They are
re-sent in full each time and paid for at full rate, because the model is
stateless — it does not remember the rules it was given on the previous move.

The variable remainder is 125 tokens (23.6%), and the part that is actually *about
the player* — their message — is 4 tokens.

## Consequences elsewhere

The same statelessness explains three other observations in this research:

- `tone_summary` must be explicitly fed back as `prior_tone` each move; the model
  has no recollection of the game's mood (`01-baseline.md`).
- A retry after a rejected answer returns the identical answer, because the retry
  is a fresh call that never learns it was rejected (`01-baseline.md`).
- Explaining an old move costs no more than explaining the current one: there is
  no history to carry, only a fixed-size set of stored facts (`06-lookback.md`).

## Caveat on the 530

The ablation prompt measured 530 tokens; logged `ok` attempts averaged 633. The
difference is the size of the candidate list and the tone summary at that point in
the game. The *shares* are what this file is for; the absolute total moves with
position.
