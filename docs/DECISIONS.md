# Decisions

Architecture decision records. Append; don't rewrite history. When a decision
is reversed, add a new ADR that supersedes the old one and mark the old one.

---

## ADR-0001 — Model access is provider-agnostic, defaulting to local

**Status:** accepted · 2026-07-25

**Context.** The bot needs a language model for every NPC line. Two options
were live: a hosted API (Anthropic) or local inference (Ollama, serving
Hermes/Qwen-class models). The development machine has an RTX 4090 (24 GB) and
61 GB of RAM, which comfortably serves a 27–32B model quantized — so local is
genuinely viable rather than aspirational.

The tradeoffs are real in both directions, and neither can be settled from
first principles. Local costs nothing per token, keeps campaign content on
hardware we own, and has no rate limit; a hosted frontier model is better at
sustained characterization and at not breaking character forty messages into a
scene. Whether a 27B model can hold "scheming innkeeper who is lying about the
missing shipment" across a long scene is an empirical question about *this*
prompt and *this* model.

**Decision.** All model calls go through `bot/engine/llm.py`, which selects a
provider from the `LLM_PROVIDER` env var. Two providers ship: `ollama` (the
default) and `anthropic` (a baseline). Prompt assembly does not branch on
provider — both reward the same static-first block ordering, and the single
`speak_as_npc` schema is enforced as constrained decoding on Ollama and as a
forced tool call on Anthropic.

**Consequences.**

- The provider question becomes an A/B test with real NPCs (issue 2.6) rather
  than an upfront bet.
- A disappointing NPC can be diagnosed: flip one env var to find out whether
  the model or the prompt is at fault. This is worth the seam on its own.
- Local inference makes the rolling-summary work (2.3) load-bearing rather than
  an optimization — a smaller context window truncates where a large one just
  costs more.
- Deployment is not settled by this ADR. A 4090 does not go in a VPS, so either
  the bot runs on the same machine as the model, or it reaches a tunnel back to
  it. Deferred until Phase 3.
- The secrets barrier (hard rule #1) gets more important, not less: smaller
  models hold a secret worse under direct questioning. Because the barrier is
  structural — the secret is never in the prompt string — this costs us
  nothing. The architecture was already right for it.

**Alternatives rejected.** Hardcoding a single provider was faster to build and
meaningfully cheaper in complexity, but converts a reversible decision into an
irreversible one at exactly the moment we have the least information.

---

## ADR-0002 — One bot account, many NPCs, via channel webhooks

**Status:** accepted · 2026-07-25

**Context.** Each NPC should appear in chat as itself — own name, own avatar —
rather than as one bot prefixing lines with a character name.

**Decision.** Use per-channel Discord webhooks with `username` and `avatar_url`
overridden per message. One webhook per channel, cached in memory by channel
ID.

**Consequences.**

- One bot account and one token, regardless of NPC count.
- Webhook personas **cannot be @-mentioned** and don't support interaction
  components. This is why the trigger rules are name-matching and reply
  detection rather than mentions — the trigger design follows from this
  decision, not from preference.
- Discord caps webhooks at 10 per channel, so creating one per message would
  hit rate limits fast. Reuse is mandatory, not an optimization.
- The bot must ignore `message.webhook_id is not None`, or it replies to its
  own NPCs forever (hard rule #3).

**Alternatives rejected.** One bot application per NPC — correct-looking, but
absurd to operate past three characters and requires a new token each time.

---

## ADR-0003 — SQLite, and every query scoped by guild

**Status:** accepted · 2026-07-25

**Context.** One developer, one D&D group, self-hosted. Not a multi-tenant
product.

**Decision.** SQLite via `aiosqlite`, SQLAlchemy 2.x async, Alembic for
migrations. Every table except `guild` carries `guild_id`, and every repository
function takes and filters on it.

**Consequences.**

- No database server to run; the whole campaign is one file you can copy.
- Migrations exist from day one, so the schema can move without hand-editing.
- The `guild_id` discipline is enforced even though there is one guild today.
  It is nearly free now and effectively impossible to retrofit correctly later
  — the failure mode is one campaign's secrets surfacing in another's scene.

**Alternatives rejected.** Postgres, which is the right answer at a scale this
project has explicitly decided not to reach. Skipping `guild_id`, which saves
almost nothing and is the exact shape of a bug you find in public.
