# CLAUDE.md — D&D Helper Bot

Always-loaded context for this repo. `AGENTS.md` is a symlink to this file, so
Codex and other agents read the same rules.

- **What to build next:** the open GitHub issues, in dependency order.
- **How the pieces fit:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **How we work:** [`docs/WORKFLOW.md`](docs/WORKFLOW.md)
- **Why things are the way they are:** [`docs/DECISIONS.md`](docs/DECISIONS.md)

## What this is

A Discord bot that acts as an AI co-DM for a tabletop RPG campaign. Two core
capabilities:

1. **Personas** — the bot roleplays NPCs, each appearing in chat with its own
   name and avatar (via channel webhooks, not multiple bot accounts).
2. **Worldbuilding memory** — persistent lore in a database, retrieved
   automatically into NPC prompts so characters remember the campaign.

Single developer, single server (his own D&D group), self-hosted. Not a public
multi-tenant product. Optimize for **clarity and iteration speed**, not scale.

## Stack (settled — do not swap without asking)

- Python 3.12+
- `discord.py` 2.x (slash commands via `app_commands`, cogs for modules)
- **Model access is provider-agnostic** behind `bot/engine/llm.py`. Ships with
  two providers: `ollama` (local, the default) and `anthropic` (metered API,
  kept as a quality baseline). See ADR-0001.
- SQLAlchemy 2.x async + `aiosqlite`, Alembic for migrations
- Pydantic v2 for all AI request/response models and config
- `pydantic-settings` + `python-dotenv` for config
- `pytest` + `pytest-asyncio`
- `ruff` (lint + format), `mypy` for `bot/engine/` and `bot/db/`

Everything is `async`. No blocking I/O in event handlers.

## Hard rules

1. **Secrets are a code-level barrier, not a prompt request.** `persona.secrets`
   and any `lore_entry` with `visibility='dm_only'` must be *physically absent*
   from the prompt string when generating for a player-facing channel. Never
   rely on instructing the model to keep a secret. There is a test for this —
   keep it passing. This matters more, not less, with a local model: smaller
   models hold secrets worse under pressure.
2. **Every query filters by `guild_id`.** Two campaigns must never see each
   other's data. No exceptions, no "it's only one server anyway."
3. **Ignore webhook messages.** In `on_message`, return early if
   `message.webhook_id is not None` or `message.author.bot`. Without this the
   bot replies to its own NPCs in an infinite loop.
4. **All model calls go through `bot/engine/llm.py`.** No `anthropic` or
   `ollama` imports anywhere else. That module owns provider selection,
   retries, token accounting, model routing, and the spend guard.
5. **Secrets come from env only.** No tokens or keys in code, tests, fixtures,
   or committed files. `.env` stays gitignored; keep `.env.example` current.
   This repo is public — treat every commit accordingly.
6. **Silence is a feature.** The bot speaks only when the trigger rules in
   `docs/ARCHITECTURE.md` match. A bot that comments on everything ruins the
   table.

## Library gotchas (verified — save yourself the debugging)

**Discord**

- `Webhook.send(content=..., username=..., avatar_url=..., wait=True)` —
  `username`/`avatar_url` override per message. This is how one bot plays many
  NPCs. `wait=True` returns a `WebhookMessage`; **always** use it so you can
  store `discord_message_id` (needed for reply-based triggers) and edit later.
- Create/fetch with `await channel.create_webhook(name="Campaign Companion")` /
  `await channel.webhooks()`. **Reuse one webhook per channel** — cache it in
  memory keyed by channel id. Discord caps webhooks per channel (10) and
  creating one per message will hit rate limits fast.
- **2000 character limit** per message. NPC monologues can exceed this — split
  on paragraph boundaries.
- Webhook personas **cannot be @-mentioned** and don't support callback-style
  components. That's why triggers are name-matching and reply-detection.
- `discord.ui.Modal` allows a **maximum of 5 `TextInput` fields.** `/npc create`
  has more than 5 attributes — see the `/npc` issue for the intended split.
- Requires **Message Content Intent** enabled in the Developer Portal *and*
  `intents.message_content = True` in code. Symptom if missed:
  `message.content` is silently empty.
- Detect "player replied to an NPC" via `message.reference.message_id` → look
  up `scene_message` → get `persona_id`.

**Ollama (default provider)**

- Structured output uses the `format` parameter with a **JSON Schema**, which
  constrains decoding at the sampler. Prefer this over asking for JSON in
  prose, and over tool-calling — it is enforced, not merely requested.
- Prefix KV caching is automatic and rewards the same prompt ordering as any
  hosted cache: **static blocks first, dynamic last**. Reordering the system
  blocks per request throws the cache away.
- Keep the model resident between requests (`keep_alive`) or you pay multi-second
  load latency on the first NPC line of every scene.
- Context window is finite and smaller than a hosted frontier model's. The
  rolling-summary work is load-bearing here, not an optimization.

**Anthropic (baseline provider)**

- Model IDs live in env vars, never hardcoded. `claude-opus-5`,
  `claude-sonnet-5`, and `claude-haiku-4-5` are the current IDs.
- Prompt caching: pass `system` as a **list of blocks**, put
  `{"cache_control": {"type": "ephemeral"}}` on the last static block. Cache
  reads cost ~10% of base input.
- **Minimum cacheable prompt length varies by model** and short prompts are
  *silently* not cached: 512 tokens for Opus 5, 1,024 for Sonnet 5, 4,096 for
  Haiku 4.5. The dialogue prompt will cache fine; don't bother adding cache
  breakpoints to small utility calls on Haiku.
- Order system blocks **static → dynamic** or caching buys nothing.
- Use **tool-use for structured output**, not "please reply with JSON."

## Conventions

- Cogs in `bot/commands/`, one file per command group. Business logic lives in
  `bot/engine/` and `bot/db/` — cogs should be thin, so logic stays
  unit-testable without a Discord connection.
- Pydantic models for every AI boundary: `PersonaCard`, `SceneContext`,
  `NpcReply`, `LoreExtraction`. Type the seams; the bug class this prevents is
  exactly the one that leaks secrets.
- DB access through repository functions in `bot/db/repo.py`. No raw SQL or
  session handling inside cogs.
- Log every model call: provider, model, input/output tokens, cache hits,
  latency, guild. Cost and latency visibility from day one.
- Structured logging via `logging` with a JSON formatter. `DEBUG` may include
  prompts, `INFO` never does.

## Definition of done for any task

- `ruff check .` and `ruff format --check .` clean
- `mypy bot/engine bot/db` clean
- `pytest` green, including a new test for what you built
- No secrets in the diff; `.env.example` updated if new config was added
- Manual verification steps from the issue actually performed in a test Discord
  server, with the result reported on the issue

## Working agreement

- **Ask before adding a dependency** or changing the schema in a way that needs
  a migration.
- One issue per branch, one branch per PR. Commit messages describe behavior,
  not files.
- If a design decision turns out to be wrong once you're in the code, **say so
  and propose the alternative** rather than silently working around it. The
  plan was written before the code existed.
- Don't build ahead. Phase 4 features (dice, character sheets, combat) are
  deliberately deferred — the schema leaves room, but implementing them early
  bloats the surface before the core feels good.
