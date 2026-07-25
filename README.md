# D&D Helper

A Discord bot that acts as an AI co-DM for a tabletop RPG campaign.

It does two things. It **plays your NPCs** — each one appears in chat under its
own name and avatar, answering in character when players talk to it. And it
**remembers your world** — lore lives in a database and is pulled into an NPC's
prompt automatically, so the innkeeper knows about the fire at the mill without
you pasting it in again.

Runs on a **local model by default** (Ollama). Nothing about your campaign has
to leave your machine.

> **Status: pre-alpha.** Phase 0 is not finished yet — there is no working bot
> to install today. The design is settled and tracked in
> [issues](../../issues); follow the [roadmap](docs/ROADMAP.md) if you want to
> watch it come together.

---

## How it works at the table

```
Kestrel Vane is behind the bar, drying a glass that was already dry.

  you ▸ Kestrel, we heard the shipment never made it past the ford.

  Kestrel Vane ▸ The glass stops. "Did you now." She sets it down,
                 too carefully. "Roads have been bad. That's all
                 that is." She doesn't look up.
```

Kestrel knows she's lying, because the DM gave her a secret. The players can't
extract it by asking nicely, or by telling the bot to ignore its
instructions — the secret is **never placed in the prompt** for a player-facing
channel. See [the secrets barrier](docs/ARCHITECTURE.md#the-secrets-barrier).

## Features

- **Personas** — `/npc create` gives a character a voice, goals, and private
  secrets. It replies when named or when a player replies to its message.
- **Silence by default** — the bot speaks only when [trigger
  rules](docs/ARCHITECTURE.md#trigger-rules-botenginerouterpy) match. A bot that
  comments on everything ruins the table.
- **Lore memory** — `/lore add` entries are scored and retrieved into the
  prompt by tag and scene, with `dm_only` entries invisible to players.
- **Rolling summaries** — long scenes get compressed so prompts plateau instead
  of growing until they truncate.
- **Local or hosted** — one env var switches between Ollama and the Anthropic
  API, with identical prompt assembly either way.

## Requirements

- Python 3.12+
- A Discord account you can create an application with
- **Either** [Ollama](https://ollama.com) with a model pulled (default), **or**
  an Anthropic API key

For local inference, plan on a GPU with 24 GB of VRAM for a 27–32B model. It
will run on less with a smaller model; NPC quality is the thing you trade away.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/njdaniel/dnd-helper.git
cd dnd-helper

make install
```

Run `make` on its own to see every target.

<details>
<summary><b>No <code>make</code>?</b> (native Windows, or a minimal container)</summary>

`make` is not installed by default on Windows. Either use WSL, or run the
commands directly — every target is a one-liner:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

alembic upgrade head               # make migrate
python -m bot.main                 # make run
pytest                             # make test
ruff check . && ruff format --check . && mypy bot/engine bot/db && pytest
                                   # make check
```

`make check` is the one worth remembering: it is exactly the definition of done
in [`CLAUDE.md`](CLAUDE.md), and the same four commands CI runs.

</details>

### 2. Create the Discord application

1. Go to the [Developer Portal](https://discord.com/developers/applications) →
   **New Application**. Name it whatever your table will see.
2. **Bot** tab → **Reset Token** → copy it. This is `DISCORD_TOKEN`. Treat it
   like a password — anyone holding it controls the bot.
3. Still on the Bot tab, scroll to **Privileged Gateway Intents** and enable
   **Message Content Intent**. ⚠️ *Skip this and the bot connects fine but
   `message.content` is silently empty — every trigger rule fails and nothing
   in the logs tells you why.*
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: **View Channels**, **Send Messages**, **Manage
     Webhooks**, **Read Message History**, **Embed Links**
   - Open the generated URL and invite the bot to your server.
5. Get your server ID for `DEV_GUILD_ID`: User Settings → Advanced → **Developer
   Mode** on, then right-click the server icon → **Copy Server ID**.

`Manage Webhooks` is the one people miss. Without it the bot can talk, but
every NPC speaks as the bot instead of as itself.

### 3. Set up a model

**Local (default):**

```bash
ollama pull qwen3.6:27b
python scripts/preflight.py
```

`qwen3.6:27b` is the known-good local model: it passed the structured-output
conformance test 10/10. Its quantized weights leave enough room for a useful
context window on a 24 GB GPU; treat **24 GB VRAM as the practical
requirement**. Quantization and context length change actual memory use, so
smaller GPUs require a smaller model and a fresh conformance run.

On the project's RTX 4090 (24 GB), `qwen3.6:27b` takes **16–36 seconds to
return a reply**. That is workable for prompt tuning and noticeable during a
live session. Keep `OLLAMA_KEEP_ALIVE` enabled to avoid adding model-load time
to the first line of each scene.

The preflight command does not start the bot or require a Discord token. It
checks the selected provider, prints the tier-to-model mapping, confirms that
Ollama is reachable and each configured model is installed, and reports free
NVIDIA VRAM when `nvidia-smi` is available. Every failed check includes the
command or environment change needed to fix it, and any failure exits non-zero.

**Hosted (optional baseline):** create a key at
[console.anthropic.com](https://console.anthropic.com) → API Keys — and **set a
monthly spend limit** under Settings → Limits while you're there. Then set
`LLM_PROVIDER=anthropic` in your `.env`. In this mode
`python scripts/preflight.py` checks that `ANTHROPIC_API_KEY` is present and
skips local Ollama checks; it does not make a metered request.

### 4. Configure

```bash
cp .env.example .env
```

Fill in `DISCORD_TOKEN` and `DEV_GUILD_ID`. Every variable is commented in
[`.env.example`](.env.example). `.env` is gitignored — keep it that way.

### 5. Create the database and run

```bash
make migrate
make run
```

Then type `/ping` in your test server. If it answers, you're set up.

---

## Development

```bash
make check
```

This runs linting, formatting checks, type checks, and the test suite. It must
pass before a PR merges — CI enforces the same commands. Tests use a fake model
provider, so `make test` never makes a real inference call.

Run `make` to list every available task. Other useful targets include
`make migrate`, `make run`, `make cli ARGS="..."`, and `make live` for the
Ollama conformance test.

Read [`CLAUDE.md`](CLAUDE.md) before contributing (or before pointing a coding
agent at this repo — `AGENTS.md` symlinks to it). It holds the hard rules,
including the ones that will cost you real money or leak your campaign's
secrets if ignored.

| Doc | What's in it |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Hard rules, stack, library gotchas, definition of done |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data model, trigger rules, prompt assembly, provider seam |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phases and acceptance gates |
| [`docs/WORKFLOW.md`](docs/WORKFLOW.md) | Branching, PRs, handing issues to agents |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | ADRs — why it's built this way |

## License

MIT — see [LICENSE](LICENSE).
