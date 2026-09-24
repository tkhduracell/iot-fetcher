# ai-brain

An always-on household agent that runs on the rpi5 next to the rest of the
iot-fetcher stack. It is not a chatbot with a prompt: it is a small set of
independent loops, each with its own markdown memory on disk, that wake on a
heartbeat, look at the house, write down what they noticed, and occasionally
ask a human for permission to do something.

## What it is

One **brain** loop plus up to five **expert** loops:

| Loop | Persona | Wakes every |
| --- | --- | --- |
| `brain` | `seed/personas/brain.md` | `BRAIN_HEARTBEAT_MIN` (default 30 min) |
| `energy` | `seed/personas/energy.md` | `EXPERT_HEARTBEAT_MIN` (default 120 min) |
| `health` | `seed/personas/health.md` | `EXPERT_HEARTBEAT_MIN` |
| `house-ops` | `seed/personas/house-ops.md` | `EXPERT_HEARTBEAT_MIN` |
| `researcher` | `seed/personas/researcher.md` | `EXPERT_HEARTBEAT_MIN` |
| `infra` | `seed/personas/infra.md` | `EXPERT_HEARTBEAT_MIN` |

Experts observe and report. They can query VictoriaMetrics, Home Assistant
(entity states over its own MCP server with `ha_context`, and the tail of its
log with `ha_error_log`, which is how a broken integration gets diagnosed
rather than guessed at), gdrive-rag and the web, write facts into their own
memory, and drop notes in the brain's inbox. `infra` and `brain` can also see
the containers this system runs in (`docker_ps`, `docker_top`, `docker_logs`)
-- everyone else's tool surface stops at the house. They cannot act on the
house and they cannot talk to Slack.
Everything those reads return is fenced as external text — the log included,
since it carries whatever an integration decided to print.

The brain reads those notes, keeps a `goals.md` it is free to rewrite, and is
the only loop that can call `propose` — which does not act either, it files a
request for a human to approve (see [Approvals](#approvals)).

The brain also has a read-only window onto the rest of the system that no
expert gets: it can read (never write) any expert's persona, journal, facts
and open gaps (`expert_overview`, `read_expert`), record a verdict on what it
finds (`review_expert` — see [Reviewing the experts](#reviewing-the-experts)),
see its own runtime state (`system_status` — cycle health, proposal loops, the
ledger), and read the whole iot-fetcher repo's tracked source (`code_overview`,
`code_list`, `code_read`, `code_grep`, `code_log` — see
[Reading the repo](#reading-the-repo)). Nothing here mutates anything but the
brain's own `reviews/` directory and a note in the reviewed expert's inbox —
the brain never edits an expert's memory directly, it corrects through a
review, the same way any other cross-loop message travels.

**Emergent goals.** Nothing hard-codes what the agents should care about. The
constitution (`seed/constitution.md`, copied to the volume on first boot and
then owned by you) is the only fixed text; `goals.md` and the brain's own
`identity.md` are rewritten by the brain over time (`rewrite_goals` and
`rewrite_identity`, both brain-only). An expert's persona is not rewritten by
anything at runtime — an expert has no tool for it, and the brain corrects an
expert through a review note, never by editing its persona file. Deleting the
volume resets it to the seed; editing `constitution.md` on the volume is how
you steer it.

**Best model first, local hardware as the fallback.** All inference runs
through a provider chain (`LLM_CHAIN`), tried in order until one answers: the
Gemini free tier until the ledger says each key's quota is gone, then a model
on a machine on the LAN, then the rpi5's own small one (see
[Local models](#local-models)). Every entry is `provider:model`
— a bare model id has no provider to dispatch to and the container refuses to
start. A shared quota ledger decides before every Gemini call whether that
model may be dialled at all, so the process stays inside the free allowance
without relying on the API to say no (see [Quota](#quota)).

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
│   ├── reviews/<expert>.jsonl the brain's verdicts on each expert (max 50 lines each)
│   └── outbox/
│       ├── <proposal-id>.json pending/terminal proposals
│       └── slack/<ts>.json    Slack posts that failed to send, retried later
├── experts/<name>/
│   ├── persona.md
│   ├── journal/YYYY-MM-DD.md
│   ├── facts/<name>.md
│   └── inbox/ (+ done/)
└── _repo/                     the code_* tools' snapshot of the public repo
    ├── current -> <sha>/      symlink, swapped atomically after each refresh
    └── <sha>/                 one extracted tarball per SHA (see below)
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
| `LLM_CHAIN` | `gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,lan:qwen3-coder:30b,ollama:llama3.2:3b` | `provider:model` entries tried in order until one answers. `lan:` is found by sweeping the network; `ollama:` is `OLLAMA_URL`. |
| `GEMINI_API_KEY` | — | Google AI Studio key. Required whenever `LLM_CHAIN` has a `gemini:` entry; the process refuses to start without it. |
| `OLLAMA_NUM_CTX` | `16384` | Context window (tokens) requested per call, via `options.num_ctx` on Ollama's native `/api/chat` endpoint, for the **`ollama:`** provider -- the rpi5's own in-compose `ollama` service (8GB RAM). Ollama's own server default is 4096 and it truncates an oversized prompt silently **from the front** -- dropping the persona/system turn a cycle needs most -- so this is set explicitly rather than left to that default. Observed cycle prompts run ~9.7k tokens, so 16384 clears that with headroom. The `ollama` service in `docker-compose.yml` also sets `OLLAMA_CONTEXT_LENGTH` to the same value as a server-side fallback for any client that does not set `options.num_ctx` -- keep the two in step. Note this option is native-API-only: the OpenAI-compatible `/v1/chat/completions` endpoint ignores per-request `options`, which is why this module talks to `/api/chat`. When a prompt is estimated to exceed the budget, the provider logs a warning and trims the oldest tool-result messages rather than letting the server truncate the persona/system turn off the front. |
| `LAN_OLLAMA_NUM_CTX` | `32768` | Same as `OLLAMA_NUM_CTX`, but for the **`lan:`** provider's host -- a desktop machine discovered on the network, not the rpi5, so a separate and larger default rather than sharing `OLLAMA_NUM_CTX`. 32768 matches what this deployment's LAN Ollama servers are themselves configured for; raise or lower to match a different LAN host's actual context window. |
| `EXPERTS` | `energy,health,house-ops,researcher,infra` | Which expert loops to start. Unset **or blank** means all five; set a shorter list, or `none`, for brain only — see [Rollout](#rollout). |
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
| `LAN_SUBNETS` | *(empty)* | Subnets to sweep for the `lan:` model's host. Empty means the /24 `HA_URL` is on — see [Local models](#local-models). |
| `LAN_SCAN_MIN` | `10` | Minutes between sweeps. |
| `CALL_TIMEOUT_S` | `60` | Seconds one model call may take before the chain falls to the next provider. Not read by `lan:` providers, which set their own (below) -- a local model can legitimately need minutes, nothing like a cloud API's SLA. |
| `CYCLE_MAX_ROUNDS` | `16` | Tool rounds one cycle may take before the loop stops waiting for `end_cycle`. |
| `CYCLE_MAX_TOKENS` | `8000` | Output tokens per round. |
| `GEMINI_THINKING_BUDGET` | `-1` | Gemini thinking budget per call: `-1` dynamic, a positive number caps it, `0` sends no `thinkingConfig`. A model that rejects the field is retried once without it. |
| `HTTP_PORT` | `8091` | Port the read-only introspection API binds inside the container. `0` disables it. Published to the LAN only by `docker-compose.local.yml`. |
| `REPO_SLUG` | `tkhduracell/iot-fetcher` | The public GitHub repo the `code_*` tools read (see [Reading the repo](#reading-the-repo)). |
| `REPO_REF` | `main` | Branch or ref the snapshot tracks. |
| `REPO_REFRESH_H` | `6` | Hours between snapshot refreshes. Refreshed once on boot too. |

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
   pause file *before* the first start. Set `EXPERTS=none` in `.env` for this
   stage, so the first boots are brain only:

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
   and give it a day. Then add `health`, then `house-ops`, then `researcher`,
   then `infra`.
   Each one has its own memory directory and its own quota appetite; adding
   them together makes it much harder to see which one is misbehaving.

5. **Drop `DRY_RUN`** once you are happy with what it is proposing.

To pause at any point: `touch volumes/ai-brain-memory/PAUSE`. It takes effect
at the top of the next round — so mid-cycle, not only at the next cycle — no
restart needed, and it does not lose queued work.

## What a cycle is supposed to do

A loop that wakes every hour and reads the same gauges will, left alone, write
"all systems operating normally" forever. Two things push against that:

- **The cycle instructions** (`CYCLE_INSTRUCTIONS` in `loop.py`, shipped in the
  image) set the bar: a cycle must end with something that was not true of its
  memory before it started. Normal readings are worth learning once, as a
  baseline; after that only departures are news. Threads beat snapshots, and
  the journal is written in the agent's own voice, opinions included.
- **An angle** is handed to each loop in the user turn — "chase one anomaly",
  "build a baseline", "pick up an open thread", "read rather than measure",
  "tend your memory". It rotates hourly and is offset per loop, so five loops
  waking together do not all take the same one, and a restart does not reset
  everyone to the first angle. It is explicitly a suggestion: a loop that is
  mid-investigation is told to ignore it.

The bar for Slack is unchanged and deliberate: a finding, not a status report.
A quiet day stays quiet.

> **Changing the personality of a running deployment.** `seed/personas/brain.md`
> and `seed/constitution.md` are copied to the volume on **first boot only** —
> after that they are yours, and a new image will not overwrite them. The cycle
> instructions and the angles, by contrast, live in the image and take effect
> on the next deploy.
>
> To put the image's seed back on a running brain, on the box:
>
> ```sh
> sudo ./ai-brain/scripts/reseed-memory.sh          # identity + constitution
> sudo ./ai-brain/scripts/reseed-memory.sh --goals  # ...and clear goals.md
> ```
>
> It backs up every file it replaces, and the next cycle picks the new text up
> without a restart. The agent rewrites `identity.md` and `goals.md` itself over
> time, so what you overwrite may be its own words rather than yours.

## Approvals

The brain never acts on the house directly. It calls `propose`, which writes
`outbox/<id>.json` with status `pending` and posts the request to your Slack
DM. You react on that message:

- ✅ (`:white_check_mark:`) — the executor runs. Currently `sonos_say`
  (speaks in `SONOS_ROOM`), `ha_todo_add` (appends to `HA_TODO_LIST`) and
  `ha_service` (one Home Assistant service call on one entity).
- ❌ (`:x:`) — rejected, nothing happens.
- No reaction for 24 hours — expired.

If Slack is unreachable when the brain proposes, the request is recorded
`failed` rather than left pending: a queued message has no `ts` for a reaction
to match, so a proposal stored against one could never be approved. The brain
gets an error back and can propose again on a later cycle.

Every terminal outcome, including rejection and expiry, drops a note into the
brain's inbox, so the agent learns what became of its request on its next cycle
rather than assuming it worked.

Before calling `propose`, the model is expected to call `list_proposals`
(pending ones, plus its most recent resolved ones) to check whether it is
about to ask for something it already asked — the exact wording it used the
first time is not tracked or matched against, so this is the model's own job,
not a code-level dedup. A still-pending proposal getting asked again just
means a second, redundant Slack message with nothing new for you to react to.

Two guarantees are worth knowing when reading logs:

- **Exactly once.** Slack redelivers, and a human can double-tap. The status
  moves out of `pending` on disk *before* the executor is awaited, so the
  second delivery finds a non-pending file and stops. A process killed
  mid-execution leaves a proposal stuck at `executing` — deliberately, since
  the safe failure is "did not retry", not "spoke twice".
- **Quiet hours.** `sonos_say` refuses between 22:00 and 07:00 Europe/Stockholm
  and records `blocked_quiet_hours`.

## Reviewing the experts

Every expert's memory is scoped to itself — it can only read and write its own
facts, journal and gaps. The brain is the one loop that can see across all of
them, and the only one whose job is to reflect on what it finds:

- **`expert_overview(name?)`** — a one-line row per expert (facts, open gaps,
  unread inbox, its last review verdict) when `name` is omitted, or one
  expert's persona preview, fact list, gaps, unread count, last cycle, recent
  journal and last review when it is given.
- **`read_expert(name, what, fact?, days?)`** — read that expert's `persona`,
  `journal` (`days` back, default 2), one named `fact`, or its `gaps`.
  Read-only: there is no tool to write into another loop's memory, brain
  included.
- **`review_expert(name, verdict, findings)`** — record a verdict (`good`,
  `stale`, `wrong`, `repetitive`, `off_goal`) in the brain's own
  `reviews/<name>.jsonl` (capped at the last 50). Anything but `good` also
  drops a note in the expert's inbox — "Brain review: `<verdict>` —
  `<findings>`. Fix or delete the affected facts." — and wakes it, the same
  delivery path `send_note` uses. The expert decides what to do with the note
  on its own next cycle; the brain never edits the expert's memory itself.

An hourly angle ("Review one expert...", see `BRAIN_ANGLES` in `loop.py`)
puts this in the brain's own rotation, so a stale or contradicted fact gets
caught even when nothing else prompts a look.

## Reading the repo

The brain can read the whole iot-fetcher source — its own code and every
sibling service's — to reason about why something behaves the way it does,
without guessing from logs and metrics alone.

- **Why the source, not the rpi5 checkout.** The live checkout on rpi5 holds
  untracked secrets — `.env` files, a service-account JSON, a password file —
  that no agent may ever see, and there is no reliable way to tell "tracked"
  from "untracked" by looking at a mounted directory. So the brain reads the
  **public GitHub tarball** instead (`codeload.github.com/<REPO_SLUG>/tar.gz/<REPO_REF>`),
  which by construction contains only what git has committed. It is
  downloaded, resolved to a commit SHA via the GitHub API, and extracted to
  `/memory/_repo/<sha>/` with a `current` symlink swapped in atomically once
  extraction succeeds — a reader mid-refresh always sees a complete snapshot,
  old or new, never a half-written one. Refreshed once on boot and then every
  `REPO_REFRESH_H` (default 6h), skipping the download when the SHA is
  unchanged; a failed refresh keeps the previous snapshot and logs the error.
- **Tar safety.** Every member is checked before it touches disk: regular
  files and directories only (no symlinks, hardlinks, devices), no absolute
  path and no `..` segment, and the resolved path must stay under the
  snapshot root. A total size cap (~50 MB) refuses an oversized archive before
  extracting a single byte. See `ai_brain/repo.py` for the full policy.
- **Sensitive-file denylist.** The snapshot already only contains what git
  tracked, but `ai_brain/sensitive.py`'s `is_sensitive(path, content=None)`
  is a second, independent gate against a secret-shaped file committed by
  mistake — applied both at extraction (the file is never written to
  `/memory/_repo` at all) and again in every `code_*` tool. `code_list` hides
  a match entirely; `code_read`/`code_grep` answer exactly as if the path did
  not exist, never a distinct "denied" that would itself confirm the file is
  there. Denied: `.env`/`.env.*`/`*.env` (but not `*.example`/`*.template`/
  `*.sample`); `*.pem`/`*.key`/`*.p12`/`*.pfx`/`*.jks`/`id_rsa*`/
  `id_ed25519*`; a filename containing `password`/`secret`/`credential`/
  `token` — except a source file (`.py`/`.ts`/`.tsx`/`.js`/`.go`/`.sh`/`.md`),
  where those words are ordinary code names (`redact.py`,
  `set-github-secrets.sh`); a small `.json` file whose content has a
  `private_key` field or a `service_account` type marker, regardless of its
  name; `.mcp.json`/`.netrc`/`.npmrc`/`.pypirc`/`.git-credentials`/
  `.htpasswd`; and anything under `volumes/` or `.git/`.
- **`code_overview()`** — call this first. Every top-level component with its
  README's first paragraph, `docker-compose.yml`'s services (image/build,
  depends_on, ports, networks, volumes and env var **names only, never
  values**), the root `CLAUDE.md`/`README.md` headings, and the CI workflows
  with the paths each triggers on.
- **`code_list(path?)`**, **`code_read(path, start?, end?)`**,
  **`code_grep(pattern, path?)`** — drill down. Repo-relative paths (e.g.
  `pool-pump-planner/vm.go`), text files only, capped at 256 KB each; `grep`
  is a Python regex capped at 100 hits. `node_modules` and lockfiles are
  skipped everywhere.
- **`code_log(n?)`** — the last N commits (sha, date, subject), so the brain
  can say "this was fixed in #548" instead of re-diagnosing it.
- **Deployed vs. main.** The snapshot tracks `REPO_REF` (`main` by default),
  which can be ahead of whatever rpi5 is actually running. `code_overview`'s
  result says so, and `code_log` is what makes the gap visible.

## Local models

The chain spends the free tier first and falls back to hardware in the house:

```
gemini:gemini-3.8-flash → gemini:gemini-3.5-flash-lite → lan:qwen3-coder:30b → ollama:llama3.2:3b
```

Each Gemini key answers until the ledger says its quota is gone (or three 429s
park it), and the local entries are what the cycle uses from then until the day
rolls over. `ollama:` is the rpi5's own service, the last resort. **`lan:` is a model on whatever machine in
the house is awake and has it pulled** — the desktop in the next room can run
something worth asking, but it is not a fixed address, so the provider goes and
finds it.

More than one `lan:` entry is fine — each gets its own sweep and its own host,
so a bigger model tried first (say, `lan:qwen3.8:27b-mlx`) falls through to a
second `lan:` entry if the machine running it is asleep, before reaching
`ollama:`. An entry whose model nothing on the network has pulled simply never
becomes available and costs the chain nothing but the sweep.

Every `LAN_SCAN_MIN` minutes the process sweeps the network: a TCP connect to
port 11434 across the subnet, then `GET /api/tags` on whatever answered. A host
counts only if it has the chain's `lan:` model pulled — a machine running Ollama
with nothing installed would otherwise be picked and then 404 every call. A
known host is re-confirmed with one request; only a host that stopped answering
costs a full sweep.

The subnet is `LAN_SUBNETS` when set, and otherwise the /24 that `HA_URL` is on
— this container's own address is a docker bridge (172.x), so sweeping its own
network would find nothing. Public ranges and anything wider than a /22 are
refused. Every scan is bounded: 30s for the scan, 20s per subnet sweep, 5s per
`/api/tags`, 0.4s per connect.

The `lan:` key gets an unmetered ledger bucket — nothing about a free tier
applies to a computer you own — and three attempts rather than the usual two.
After the third failure the host is forgotten, the next sweep looks for another
one, and the cycle falls through to the rest of the chain. Until a host is
found the provider reports itself unavailable, so the chain steps past it
without spending anything.

Its own call timeout is far longer than the chain's default (900s, ignoring
`CALL_TIMEOUT_S`): a big model on real hardware can take minutes to answer,
and a bound sized for a cloud API would kill it mid-thought. The *connect*
phase stays tight regardless (3s) — a host not answering the port at all is
never worth 900s of patience. The loop's own outer bound per round is
computed from every provider's actual timeout and attempt count, so a slow
`lan:` entry does not get cut off by a ceiling sized for the fast ones ahead
of it in the chain.

`GET /api/status` reports what it found, one entry per `lan:` model in
`lan_host.hosts[].host`/`.model`/`.subnets`, which is where to look when you
expect the desktop to be answering and Gemini is.

Drop the `lan:` entry from `LLM_CHAIN` to switch the sweep off entirely.

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
- **Effort costs requests.** Five loops and up to `CYCLE_MAX_ROUNDS` rounds per
  cycle spend the free `RPD` well before the day is out. That is the design —
  the ledger starves the experts first and the chain falls through to
  flash-lite and then to the local ollama model, so the brain keeps thinking on
  a drained budget. Lower `CYCLE_MAX_ROUNDS`, lengthen the heartbeats or shorten
  `EXPERTS` if you would rather it stayed on the strong model all day.
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
| `ai_brain_loop_tokens_total` | `loop`, `kind` | Tokens each loop has spent since start (`prompt`, `completion`). Resets on restart; use `increase()`. The ledger counts per key; this is what says which loop is expensive. |
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
| `GET /api/agents/{name}` | That summary plus identity, goals, fact names, unread inbox, journal dates and the live trace. For an expert, also the brain's most recent `review_expert` verdict on it (`last_review`, `null` if never reviewed). |
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
