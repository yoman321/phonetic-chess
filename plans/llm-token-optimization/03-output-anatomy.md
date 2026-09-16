<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Output anatomy: what fills the reply

## Field composition (6 live calls)

Visible output averaged **84 tokens / 359 characters**.

| Field | Avg chars | Share of JSON |
|---|---|---|
| `rationale` | 119 | 33.1% |
| `tone_summary` | 117 | 32.7% |
| `intent` | 64 | 17.9% |
| JSON syntax (braces, keys, quotes) | 55 | 15.3% |
| **`uci` — the actual move** | **4** | **1.1%** |

A real reply, in full:

```json
{"uci":"d8h4","tone_summary":"The game shifts into a high-energy assault as Black
launches a direct check from the queen, mirroring White's earlier boldness.",
"intent":"The new message signals a decisive, attacking stance.",
"rationale":"Moving the queen to h4 delivers an immediate check, embodying..."}
```

359 characters to say `d8h4`. This mirrors the input finding: the player's message
is 0.8% of what is sent, and the move is 1.1% of what comes back.

## Per move, all output

| Component | Tokens | Note |
|---|---|---|
| The move (`uci`) | ~4 | 2% of output |
| Three prose fields | ~98 | all three are used — see below |
| Hidden reasoning | ~92 | discarded before anyone sees it |
| **Total** | **194** | |

## Is the prose waste? No — but one part is optional

Checked before concluding. All three prose fields are consumed:

- **`tone_summary`** is fed back as `prior_tone` on the next move
  (`sessions_ops.py:336`). It is the game's rolling memory and is load-bearing on
  every turn whether or not anyone reads it. **Cannot be made lazy.**
- **`intent`** and **`rationale`** are rendered in the chat panel
  (`Chatbox.jsx:52-64`) as "Message intent:" and "Why this move:" — but only
  inside a card that is collapsed until the player presses the `?` button
  (`openIdx` starts null). Generated on 100% of moves; displayed on an unknown
  fraction. **Candidate for lazy generation — see `05-ab-lazy-explain.md`.**

`tone_summary` itself is never displayed directly; the player sees it one move
later, under the "Prior tone:" label.

## The genuinely wasted output

The ~92 hidden reasoning tokens. `reasoning_format: "hidden"` (`llm.py:206`)
strips the model's chain of thought from the response so `json.loads` does not
choke on it — but it is still generated, counted, and charged against the rate
limit. **47% of output tokens, ~10% of all tokens per move**, for text that is
deleted before delivery.

Already at the lowest setting this model accepts. The shipped qwen config used
`reasoning_effort=none` specifically to avoid this (`llm.py:16-20`); `gpt-oss`
rejects `none` with a 400.

## Measuring this from production data

`llm_call_attempts.raw_content` stores the complete JSON reply on every successful
call, so the field split is recoverable retroactively with no new instrumentation:

```sql
WITH ok AS (
  SELECT completion_tokens, reasoning_tokens, raw_content,
         raw_content::jsonb AS j
  FROM llm_call_attempts
  WHERE outcome = 'ok' AND raw_content IS NOT NULL
)
SELECT round(100.0*avg((length(j->>'intent')+length(j->>'rationale'))::numeric
                       / length(raw_content)), 1) AS ir_pct_chars,
       round(avg((completion_tokens - COALESCE(reasoning_tokens,0))
                 * (length(j->>'intent')+length(j->>'rationale'))::numeric
                 / length(raw_content)))          AS ir_tok_est
FROM ok;
```

Run against a fresh 20-move game: JSON averaged 400 chars, `intent`+`rationale`
192 chars = **48.0%**; output 168 tok (76 reasoning, 92 visible); estimated
**44 tokens** for the two fields.

That query answers about half the question. What it cannot reach: the reasoning
attributable to those fields (`reasoning_tokens` is one opaque total), and the
input-side instruction cost (the prompt text is not stored — though every input to
it is, so it is reconstructable). See `08-reliability.md`.
