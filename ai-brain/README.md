# ai-brain

An always-on household agent that runs on the rpi5 next to the rest of the
iot-fetcher stack. It is not a chatbot with a prompt: it is a small set of
independent loops, each with its own markdown memory on disk, that wake on a
heartbeat, look at the house, write down what they noticed, and occasionally
ask a human for permission to do something.

## What it is

One **brain** loop plus up to four **expert** loops:

| Loop | Persona | Wakes every |
| --- | --- | --- |
| `brain` | `seed/personas/brain.md` | `BRAIN_HEARTBEAT_MIN` (default 30 min) |
| `energy` | `seed/personas/energy.md` | `EXPERT_HEARTBEAT_MIN` (default 120 min) |
| `health` | `seed/personas/health.md` | `EXPERT_HEARTBEAT_MIN` |
| `house-ops` | `seed/personas/house-ops.md` | `EXPERT_HEARTBEAT_MIN` |
| `researcher` | `seed/personas/researcher.md` | `EXPERT_HEARTBEAT_MIN` |

Experts observe and report. They can query VictoriaMetrics, Home Assistant,
gdrive-rag and the web, write facts into their own memory, and drop notes in
the brain's inbox. They cannot act on the house and they cannot talk to Slack.

The brain reads those notes, keeps a `goals.md` it is free to rewrite, and is
the only loop that can call `propose` — which does not act either, it files a
request for a human to approve (see [Approvals](#approvals)).

**Emergent goals.** Nothing hard-codes what the agents should care about. The
constitution (`seed/constitution.md`, copied to the volume on first boot and
then owned by you) is the only fixed text; `goals.md` and every persona file
are rewritten by the brain over time. Deleting the volume resets it to the
seed; editing `constitution.md` on the volume is how you steer it.

**Gemini free tier.** All inference runs through a provider chain
(`LLM_CHAIN`, default `gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite`)
against a free-tier key. Every entry is `provider:model` — a bare model id has
no provider to dispatch to and the container refuses to start. A shared quota ledger decides before every call whether a model
may be dialled at all, so the process stays inside the free allowance without
relying on the API to say no (see [Quota](#quota)).

## Memory layout

Everything lives on the `/memory` volume (`ai-brain-memory`, bind-mounted to
`./volumes/ai-brain-memory` locally). All of it is plain markdown and JSON —
`cat` is the debugger.

```
/memory
├── constitution.md            copied from seed on first boot, then yours
├── PAUSE                      if this file exists, every loop skips its cycle
├── _ledger.json               quota state, rewritten atomically
├── brain/
│   ├── identity.md            the brain's persona; it rewrites this
│   ├── goals.md               emergent goals; it rewrites this too
│   ├── journal/YYYY-MM-DD.md  one line per cycle outcome
│   ├── facts/<name>.md        long-lived notes (max 40 before compaction)
│   ├── inbox/                 unread notes from experts and approvals
│   │   └── done/              processed notes, purged after 30 days
│   └── outbox/
│       ├── <proposal-id>.json pending/terminal proposals
│       └── slack/<ts>.json    Slack posts that failed to send, retried later
└── experts/<name>/
    ├── persona.md
    ├── journal/YYYY-MM-DD.md
    ├── facts/<name>.md
    └── inbox/ (+ done/)
```

Journals keep the last 30 files — older ones are deleted after every cycle —
and facts are capped at 40 before the loop is told to compact. Both limits are
clearable rather than one-way: the agent has `delete_fact` for the fact side,
and the journal prunes itself, so the "your memory is large" hint stops firing
once it has done the work. Every write goes through a temp file plus `os.replace`, so a
reader never sees a half-written file and a crash never leaves a torn ledger.

## Configuration

Copy `.env.example` to `.env`. Every variable below is read by
`ai_brain.config.load_settings`; blank means no default.

| Variable | Default | What it does |
| --- | --- | --- |
| `MEMORY_ROOT` | `/memory` | Where agent memory lives. The volume mount point. |
| `SEED_ROOT` | `/app/seed` (set in the image) | Starting constitution and personas. |
| `LLM_CHAIN` | `gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite` | `provider:model` entries tried in order until one answers. |
| `GEMINI_API_KEY` | — | Google AI Studio key. Required whenever `LLM_CHAIN` has a `gemini:` entry; the process refuses to start without it. |
| `EXPERTS` | *(empty)* | Which expert loops to start, from `energy`, `health`, `house-ops`, `researcher`. Empty means brain only — see [Rollout](#rollout). |
| `BRAIN_HEARTBEAT_MIN` | `30` | Minutes between brain cycles. |
| `EXPERT_HEARTBEAT_MIN` | `120` | Minutes between each expert's cycles. |
| `VM_URL` | `http://database-auth:8427` | VictoriaMetrics through vmauth. |
| `INFLUX_TOKEN` | — | Bearer token for VM reads and for writing its own metrics. |
| `HA_URL` | `http://192.168.68.87:8123` | Home Assistant base URL. |
| `HA_TOKEN` | — | Long-lived HA access token. |
| `HA_TODO_LIST` | `todo.shopping_list` | Entity the `ha_todo_add` executor appends to. |
| `GDRIVE_RAG_URL` | `http://gdrive-rag:8090` | Document search backend. |
| `SONOS_URL` | `http://sonos-http-api:5005` | Sonos HTTP API for the `sonos_say` executor. |
| `SONOS_ROOM` | `Kitchen` | Room that speaks. |
| `BRAVE_API_KEY` | — | Web search for the researcher. Omit to disable it. |
| `SLACK_BOT_TOKEN` | — | `xoxb-…`. Without both Slack tokens, Slack is skipped entirely. |
| `SLACK_APP_TOKEN` | — | `xapp-…`, Socket Mode. |
| `SLACK_USER_ID` | — | Your Slack user id; the only DM the brain talks to. |
| `DRY_RUN` | `0` | Exactly `"1"` means no side effect leaves the process. |
| `RPM` | `8` | Requests per minute, per model key. |
| `TPM` | `200000` | Tokens per minute, per model key. |
| `RPD` | `200` | Requests per day, per model key. |
| `CALL_TIMEOUT_S` | `60` | Seconds one model call may take before the chain falls to the next provider. |
| `HTTP_PORT` | `8091` | Port the read-only introspection API binds inside the container. `0` disables it. Published to the LAN only by `docker-compose.local.yml`. |

## Slack app setup

The brain talks to exactly one person over a Slack DM, using Socket Mode — so
no inbound URL and nothing to expose from the rpi5.

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app
   manifest**, pick your workspace, and paste `slack-manifest.yml`.
2. Confirm **Socket Mode** is enabled under *Settings → Socket Mode* (the
   manifest sets `socket_mode_enabled: true`, but check it took).
3. *Basic Information → App-Level Tokens* → **Generate Token and Scopes**, add
   the `connections:write` scope, and copy the `xapp-…` token into
   `SLACK_APP_TOKEN`.
4. *Install App* → install to the workspace, then copy the **Bot User OAuth
   Token** (`xoxb-…`) into `SLACK_BOT_TOKEN`.
5. Find your own user id: click your avatar in Slack → **Profile** → the *⋮*
   menu → **Copy member ID**. It looks like `U01ABCDEFGH`. Put it in
   `SLACK_USER_ID`.
6. Open a DM with the bot so it can message you.

> The manifest has **not been validated against Slack live yet.** If the import
> is rejected, the field names are the thing to check first. Note also that
> `agents.sessions.*` API errors are deliberately swallowed — if threads show
> up without a title, the likely cause is a wrong method name in `slack_io.py`,
> not a permissions problem.

## Rollout

Bring it up deliberately. The whole point of the design is that it accumulates
state, so it is much easier to start narrow than to unpick a bad first day.

1. **Deploy paused, brain only.** Create the volume directory and drop the
   pause file *before* the first start. `.env.example` already ships
   `EXPERTS=` empty, so a straight copy gives you brain only:

   ```sh
   mkdir -p volumes/ai-brain-memory
   sudo chown -R 1000:1000 volumes/ai-brain-memory
   touch volumes/ai-brain-memory/PAUSE
   sudo docker compose -f docker-compose.yml -f docker-compose.local.yml up -d ai-brain
   ```

   The container runs as the non-root user `brain` (uid 1000), so the bind
   mount has to be writable by uid 1000 — that is what the `chown` is for.
   Without it the first boot cannot seed the constitution and the container
   exits.

   With `PAUSE` present, every loop wakes, records `paused` in its journal, and
   goes back to sleep without calling a model. This is a safe way to confirm
   the container starts, Slack connects, and the volume is writable.

2. **Check the seeding.** `ls volumes/ai-brain-memory` should now show
   `constitution.md`, `brain/` and `_ledger.json`. Read
   `constitution.md` and edit it to taste — from here on it is yours, and the
   seed will never overwrite it.

3. **Let the brain think.** `rm volumes/ai-brain-memory/PAUSE`. Watch the
   journal for a few cycles (see [Inspecting](#inspecting)) and check that the
   Slack DM behaves. `DRY_RUN=1` is worth keeping on for this stage.

4. **Enable experts one at a time.** Set `EXPERTS=energy` in `.env`, restart,
   and give it a day. Then add `health`, then `house-ops`, then `researcher`.
   Each one has its own memory directory and its own quota appetite; adding
   them together makes it much harder to see which one is misbehaving.

5. **Drop `DRY_RUN`** once you are happy with what it is proposing.

To pause at any point: `touch volumes/ai-brain-memory/PAUSE`. It takes effect
at the top of the next round — so mid-cycle, not only at the next cycle — no
restart needed, and it does not lose queued work.

## Approvals

The brain never acts on the house directly. It calls `propose`, which writes
`outbox/<id>.json` with status `pending` and posts the request to your Slack
DM. You react on that message:

- ✅ (`:white_check_mark:`) — the executor runs. Currently `sonos_say`
  (speaks in `SONOS_ROOM`) and `ha_todo_add` (appends to `HA_TODO_LIST`).
- ❌ (`:x:`) — rejected, nothing happens.
- No reaction for 24 hours — expired.

If Slack is unreachable when the brain proposes, the request is recorded
`failed` rather than left pending: a queued message has no `ts` for a reaction
to match, so a proposal stored against one could never be approved. The brain
gets an error back and can propose again on a later cycle.

Every terminal outcome, including rejection and expiry, drops a note into the
brain's inbox, so the agent learns what became of its request on its next cycle
rather than assuming it worked.

Two guarantees are worth knowing when reading logs:

- **Exactly once.** Slack redelivers, and a human can double-tap. The status
  moves out of `pending` on disk *before* the executor is awaited, so the
  second delivery finds a non-pending file and stops. A process killed
  mid-execution leaves a proposal stuck at `executing` — deliberately, since
  the safe failure is "did not retry", not "spoke twice".
- **Quiet hours.** `sonos_say` refuses between 22:00 and 07:00 Europe/Stockholm
  and records `blocked_quiet_hours`.

## Quota

A single `Ledger` (persisted at `/memory/_ledger.json`) is the accountant for
every model key. Nothing calls a provider without asking it first.

- **Budgets** are per key: `RPM` requests/minute, `TPM` tokens/minute (both on
  a rolling 60-second window) and `RPD` requests/day, plus a synthetic daily
  token cap so experts can be starved before the brain is.
- **Experts starve first.** An expert may only spend while both remaining
  daily fractions are above 40%. The last 40% of each day belongs to the brain.
- **A 429 is authoritative.** The server's `Retry-After` wins over local
  arithmetic; without one, backoff doubles from 60 s up to an hour. Three
  consecutive 429s park the key until the next reset regardless of what the
  ledger thought was left — the API knows better than we do.
- **A missing model (404)** disables that key for 24 hours, since a config
  typo will not fix itself by retrying.
- **The day rolls at midnight US/Pacific**, which is when Google resets the
  free tier — not local midnight. The brain gets a `new day, budget restored`
  note in its inbox when it turns, so it can see the constraint lift. A corrupt ledger file is treated as "half
  the day is already spent" rather than as a fresh budget.

## Inspecting

Read today's brain journal (substitute the date you want):

```sh
cat volumes/ai-brain-memory/brain/journal/2026-09-07.md
```

Same for an expert: `volumes/ai-brain-memory/experts/energy/journal/…`.
Pending proposals are `volumes/ai-brain-memory/brain/outbox/*.json`, and
current quota state is `volumes/ai-brain-memory/_ledger.json`.

Container logs: `sudo docker logs -f ai-brain`.

Metrics are written to VictoriaMetrics every 60 seconds (best-effort — a VM
outage never stops the brain thinking):

| Metric | Labels | Meaning |
| --- | --- | --- |
| `ai_brain_cycle_total` | `loop`, `status` | Counter of cycle outcomes (`ok`, `paused`, `error`, …). Flat `ok` means the brain has stopped thinking. |
| `ai_brain_ledger_remaining` | `model`, `kind` | Fraction of today's requests/tokens left, 0–1. |
| `ai_brain_loop_last_cycle_seconds` | `loop` | Age of the last completed cycle. Absent until a loop has finished one — a zero would read as "just ran", which is the opposite of the truth. |

## HTTP API

A read-only window onto the running process, served on `HTTP_PORT` (default
`8091`). Every route is a `GET` and nothing mutates anything — anything else is
a 405. There is no authentication, which is why the port is published only by
`docker-compose.local.yml` and so reaches the LAN, never the internet. Set
`HTTP_PORT=0` to switch it off; a port that cannot be bound is logged and the
brain carries on without it.

| Route | Answers |
| --- | --- |
| `GET /healthz` | `{ok, uptime_s, loops[]}` — is the process up, and which loops does it run. |
| `GET /api/status` | Uptime, pause state, Slack, the quota ledger per model key, proposal counts and a fixed allowlist of settings. Never any token. |
| `GET /api/agents` | One summary per agent, brain first: last cycle, next wake, cycle counts, whether a cycle is running now. |
| `GET /api/agents/{name}` | That summary plus identity, goals, fact names, unread inbox, journal dates and the live trace. |
| `GET /api/agents/{name}/journal?days=N` | The last `N` days it actually wrote, newest first. `days` defaults to 3 and is clamped to 1–30; a non-integer is a 400. |
| `GET /api/agents/{name}/trace` | The current cycle's thoughts, or the last one's. `finished_at: null` means it is still thinking. |
| `GET /api/agents/{name}/facts/{fact}` | One fact's markdown. |
| `GET /api/proposals` | Every proposal, newest first (capped at 100), with pending and total counts. |
| `GET /api/slack/sessions` | The topic-to-thread map and how many posts are queued. `configured: false` when Slack is off — a 200, not an error. |

The trace is in memory only and is replaced at the start of every cycle: the
journal is the history, this is the live view. From the box:

```sh
curl -s http://localhost:8091/api/agents/brain/trace
```

## Development

```sh
uv sync              # install, including dev deps
make test            # uv run pytest -q
make build           # build the image
make dev             # run the built image against ../volumes with DRY_RUN=1
```

`make push` pushes to `europe-docker.pkg.dev/…/images/ai-brain:latest`, but CI
already builds and pushes on every merge to `main` that touches `ai-brain/**`
(`.github/workflows/build-ai-brain.yml`), so deploying is normally just a pull
and `up -d` on the rpi5.
