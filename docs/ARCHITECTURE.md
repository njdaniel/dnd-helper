# Architecture

Reference for how the pieces fit. Task-level work lives in GitHub issues, not
here — this document describes the *system*, not the schedule. See
[`ROADMAP.md`](ROADMAP.md) for phasing.

---

## Shape of the thing

```
Discord ──▶ cogs (bot/commands/) ──▶ engine (bot/engine/) ──▶ db (bot/db/)
                                          │
                                          ├─▶ router.py   should an NPC speak?
                                          ├─▶ persona.py  build the prompt
                                          ├─▶ memory.py   retrieve lore
                                          ├─▶ llm.py      call a model
                                          └─▶ speech.py   post as the NPC
```

Cogs are thin. They parse Discord input, call into `engine/`, and render the
result. Everything worth testing lives in `engine/` and `db/`, which know
nothing about Discord and can be unit-tested without a connection.

### Repo layout (target)

```
dnd-helper/
├── CLAUDE.md               agent context (AGENTS.md symlinks here)
├── docs/
├── pyproject.toml          deps + ruff/mypy/pytest config
├── alembic.ini
├── bot/
│   ├── main.py             entrypoint: intents, cog loading, run
│   ├── config.py           Pydantic Settings from env
│   ├── commands/
│   │   ├── npc.py          /npc *
│   │   ├── lore.py         /lore *
│   │   ├── scene.py        /scene *
│   │   └── say.py          /say, /recap
│   ├── engine/
│   │   ├── llm.py          provider selection, routing, retries, spend guard
│   │   ├── providers/      ollama.py, anthropic.py
│   │   ├── router.py       trigger rules — should an NPC speak?
│   │   ├── persona.py      prompt assembly + reply generation
│   │   ├── memory.py       lore retrieval, ingestion, summarization
│   │   ├── speech.py       webhook posting, chunking, per-channel queue
│   │   └── schemas.py      Pydantic models for AI boundaries
│   └── db/
│       ├── models.py       SQLAlchemy models
│       ├── repo.py         query functions (all guild_id-scoped)
│       ├── session.py      async engine/session factory
│       └── migrations/     Alembic
└── tests/
    ├── conftest.py         in-memory DB fixture, fake LLM provider
    ├── test_secrets_barrier.py
    ├── test_router.py
    ├── test_memory.py
    └── test_prompt_assembly.py
```

---

## Provider seam

`bot/engine/llm.py` is the only module that knows a model exists. It exposes
one entry point and selects a provider from config:

```python
async def complete(
    purpose: str,                 # for the usage log: "dialogue" | "recap" | ...
    system_blocks: list[str],     # ordered static → dynamic
    messages: list[Message],
    schema: dict,                 # JSON Schema the reply must satisfy
    tier: Literal["dialogue", "utility", "epic"],
) -> NpcReply
```

Providers implement the same protocol and differ only in how they enforce
structure and where caching happens:

| | `ollama` (default) | `anthropic` (baseline) |
|---|---|---|
| Structured output | `format=<JSON Schema>` — constrained at the sampler | forced `speak_as_npc` tool call |
| Prompt caching | automatic KV prefix reuse | explicit `cache_control` on last static block |
| Cost | electricity | metered per token |
| Failure mode | context overflow, slow first token | rate limits, spend cap |

Both reward the same prompt ordering (static first), so `persona.py` does not
branch on provider. Keeping providers behind one protocol is what makes "is the
local model good enough?" an experiment rather than a rewrite — see ADR-0001.

### Local model baseline

`qwen3.6:27b` is the known-good Ollama baseline: it passed the structured-output
conformance test 10/10. Plan on 24 GB of VRAM so its quantized weights and a
useful context window fit together. A smaller model may make lower-VRAM
hardware viable, but it must pass the conformance test before becoming a
supported choice. Availability is checked separately with
`python scripts/preflight.py`; preflight intentionally does not repeat the
quality test.

---

## Data model

SQLAlchemy models in `bot/db/models.py`. All tables except `guild` carry a
`guild_id` FK, and **every query filters on it** (hard rule #2).

```
guild
  id (pk), discord_guild_id (unique), name, content_rating (default 'pg13'),
  dm_role_id (nullable), ambient_enabled (bool, default False),
  daily_reply_budget (int, default 200), created_at

persona
  id (pk), guild_id (fk), name, avatar_url (nullable),
  status ('active'|'retired', default 'active'),
  public_desc (text)        -- what players could know about them
  personality (text)        -- voice, mannerisms, speech patterns
  goals (text)              -- motivations driving behavior
  secrets (text, nullable)  -- DM-ONLY. See hard rule #1.
  knowledge_tags (json)     -- lore tags this NPC plausibly knows
  created_by, created_at, updated_at
  UNIQUE(guild_id, name)    -- name is the trigger key; must be unambiguous

lore_entry
  id (pk), guild_id (fk), title, body (text),
  category ('location'|'faction'|'person'|'event'|'item'|'rule'|'other'),
  tags (json), visibility ('public'|'dm_only', default 'public'),
  source ('manual'|'extracted'|'recap', default 'manual'),
  image_url (nullable)      -- location art, maps, handouts. See #35.
  created_by, created_at, updated_at
  INDEX(guild_id, category)

scene
  id (pk), guild_id (fk), channel_id, title (nullable),
  status ('active'|'paused'|'ended', default 'active'),
  mode ('standard'|'epic', default 'standard'),
  location_lore_id (fk nullable), summary (text, default ''),
  image_url (nullable)      -- establishing shot for the scene embed
  created_at, updated_at
  UNIQUE(channel_id) WHERE status='active'   -- one live scene per channel

scene_persona            -- which NPCs are "on stage"
  scene_id (fk), persona_id (fk), PRIMARY KEY(scene_id, persona_id)

scene_message
  id (pk), scene_id (fk), discord_message_id (unique),
  author_type ('player'|'npc'|'dm'|'system'),
  author_name, persona_id (fk nullable), content (text), created_at
  INDEX(scene_id, created_at)

usage_log
  id (pk), guild_id (fk), provider, model, tier, input_tokens,
  cache_read_tokens, output_tokens, latency_ms, purpose, created_at
  -- `tier` is stored, not derived: all three tiers may point at the same
  -- model (the local default does), so model→tier is ambiguous.

-- Phase 4 only; do not implement yet, but don't design it out:
character_sheet
  id (pk), guild_id (fk), owner_user_id, name, data_json, created_at, updated_at
```

### Images

The database stores **URLs, not bytes**, and that is a constraint rather than a
preference: Discord webhooks take `avatar_url` as a URL their servers fetch, and
there is no way to pass raw image data for a per-message avatar override. A
portrait on local disk cannot be used as an NPC's face.

Three columns carry images — `persona.avatar_url`, `lore_entry.image_url`,
`scene.image_url` — and all three need a publicly reachable host. Where that
host is remains open (#35), and is entangled with where the bot ends up running
(#34): a bot on a VPS can serve its own images; a bot on a desktop cannot,
without a tunnel.

Until #35 is settled, any publicly-fetchable URL works. Nothing in the schema
changes when the answer arrives.

---

## Trigger rules (`bot/engine/router.py`)

Given an `on_message` event, decide whether an NPC speaks. Evaluate in order;
**first match wins.**

0. **Bail out** if `message.author.bot` or `message.webhook_id is not None`
   (hard rule #3), if content starts with `((` (out-of-character), or if there
   is no `active` scene for this channel.
1. **Reply trigger** — `message.reference` points to a `scene_message` with
   `author_type='npc'` → that persona responds.
2. **Name trigger** — message contains an on-stage persona's name
   (case-insensitive, word-boundary matched). If multiple names match, the
   **last-mentioned** one responds. Only one NPC responds per message in V1.
3. **Ambient** — if `guild.ambient_enabled` and ≥10 player messages have passed
   since the last NPC line, one on-stage NPC may react. Default off.
4. **Otherwise: stay silent.** Still log the message to `scene_message` for
   context.

Explicit commands (`/say`, `/npc speak`) bypass the router entirely.

This module is pure logic over fabricated message objects — no Discord
connection, no database round-trip in the decision path. **Keep it that way.**
It is the cheapest thing in the system to test and the most annoying to debug
in production.

---

## Prompt assembly (`bot/engine/persona.py`)

Build `system` as an ordered list of blocks, **static first** so caching works
(both providers, for different mechanical reasons):

| # | Block | Cacheable | Contents |
|---|-------|-----------|----------|
| 1 | Game-master instructions | yes | Stay in character; second/third-person narration; never speak for player characters; never invent mechanics or resolve dice — defer to the DM; respect content rating; 1–3 short paragraphs max |
| 2 | Persona card | yes | name, public_desc, personality, goals |
| 3 | DM-only material | yes | `persona.secrets` + `dm_only` lore — **INCLUDED ONLY IF the channel is DM-only** |
| 4 | Retrieved lore | no | top-k entries from memory retrieval |
| 5 | Scene card | no | location, who is on stage, rolling `summary` |

On Anthropic, put the `cache_control` breakpoint on the **last static block
included** (block 2 for player-facing scenes, block 3 for DM channels). On
Ollama the prefix cache handles this implicitly — but only if block order is
stable, so don't reorder per request.

`messages` = last 20–40 `scene_message` rows rendered as `Speaker: content`,
then the triggering message.

### The secrets barrier

`assemble_prompt(persona, scene, is_dm_context: bool)` takes visibility as an
**explicit parameter** and omits blocks/entries entirely when `is_dm_context`
is False. Do not pass secrets in and instruct the model to withhold them.

`test_secrets_barrier.py` asserts that a unique sentinel string stored in
`persona.secrets` and in a `dm_only` lore entry does **not** appear anywhere in
the assembled player-facing prompt. Not that the model declined to say it —
that it is not in the string.

An NPC can still *act* on a secret — behave evasively, lie, change the subject —
because `goals` and `personality` encode the behavior without stating the fact.
This is the design that makes a local model safe to use: it cannot leak what it
was never given.

### Structured output

One schema, enforced differently per provider:

```json
{
  "name": "speak_as_npc",
  "description": "Deliver this NPC's line in the scene.",
  "input_schema": {
    "type": "object",
    "properties": {
      "line": {
        "type": "string",
        "description": "What the NPC says and does, in prose. Under 1200 characters."
      },
      "mood": { "type": "string" },
      "memory_notes": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Facts newly established in the fiction that should be remembered. Empty if nothing new."
      }
    },
    "required": ["line"]
  }
}
```

Ollama passes `input_schema` as `format`; Anthropic registers it as a forced
tool call. Either way the result parses into an `NpcReply` Pydantic model, and
a parse failure is an error the caller handles — never a string fed to Discord.

---

## Cost and latency

Local inference has no marginal cost, so the `usage_log` table and `/usage`
command exist for **latency and context-growth** visibility rather than
billing. Watch for prompt size growing linearly across a long session: that
means the rolling summary isn't firing, and on a local model with a finite
context window it ends in truncation rather than a bigger invoice.

If you switch `LLM_PROVIDER=anthropic`, the same table starts answering the
money question. Target for one active campaign is roughly $5–12/month; if
`/usage` extrapolates well past that, the likely culprits are cache misses from
reordered system blocks or an oversized context window.
