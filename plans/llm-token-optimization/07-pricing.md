<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Paid-tier reference — NOT currently applicable

**The project is on Groq's free tier. Nothing here affects anything today.**

This file exists for one reason: the 2026-09-16 model decision
(`09-model-and-max-tokens.md`) is explicitly conditional — *stay on qwen while on
the free tier, revisit if the project ever moves to a paid tier.* That revisit
needs a number, and this is it.

Everywhere else in this directory, figures are in **tokens**, which is what the
free tier actually constrains.

## Rates (fetched from Groq model docs, 2026-09-16)

| Model | Input /1M | Output /1M | Prompt caching? |
|---|---|---|---|
| `openai/gpt-oss-20b` | $0.075 | $0.300 | yes |
| `openai/gpt-oss-120b` | $0.150 | $0.600 | yes |
| `qwen/qwen3.8-27b` | $0.800 | $4.000 | **no** |

## The one implication

**Qwen is roughly 8.4x the per-move cost of `gpt-oss-20b`**, and gets no prompt
caching. On a paid tier that reverses the model decision. On the free tier it is
irrelevant, which is why the decision stands.

The stale `$3.00/1M` note at `llm.py:17` refers to `qwen/qwen3.6-27b`, which no
longer exists on this account. It should not be trusted.

## Prompt caching availability

Caching is **gpt-oss only** (GPT-OSS 20B / 120B / Safeguard 20B): automatic, no
code change, 50% discount on cached input, minimum cacheable prefix 128-1024
tokens depending on model.

That discount is on **billing**, and it is not established that cached tokens are
excluded from the **rate limit** — which is the only thing that binds on the free
tier. That open question is the subject of `10-kv-caching.md`, and it matters far
more than the rates above.
