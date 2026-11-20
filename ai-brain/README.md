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
| `brain` | `seed/personas/brain.md` | `BRAIN_HEARTBEAT_MIN` (default 240 min) |
| `energy` | `seed/personas/energy.md` | `EXPERT_HEARTBEAT_MIN` (default 120 min) |
| `health` | `seed/personas/health.md` | `EXPERT_HEARTBEAT_MIN` |
| `house-ops` | `seed/personas/house-ops.md` | `EXPERT_HEARTBEAT_MIN` |
| `researcher` | `seed/personas/researcher.md` | `EXPERT_HEARTBEAT_MIN` |
| `infra` | `seed/personas/infra.md` | `EXPERT_HEARTBEAT_MIN` |

Experts observe and report, and each has only its own slice of the tools
(`loops=` in `tools/`): VictoriaMetrics (`vm_query`, `vm_metrics`) for `energy`,
`health` and `house-ops`; Home Assistant for `house-ops` (entity states over its
own MCP server with `ha_context`, and the tail of its log with `ha_error_log`,
which is how a broken integration gets diagnosed rather than guessed at);
gdrive-rag (`drive_search`) and the web (`web_search`, `web_fetch`) for the
`researcher` alone, which has no metrics or Home Assistant; the Airbnb calendar
for `researcher`, `energy` and `house-ops`. Every expert writes facts into its
own memory and drops notes in the brain's inbox. `infra` and `brain` can also
see the containers this system runs in (`docker_ps`, `docker_top`,
`docker_logs`) -- `infra`'s whole tool surface -- and the brain has the metrics
and Home Assistant tools too, but not the web or Drive: it asks the researcher
by note. A container name is
checked against Docker's own name rule before any request is built (in the
tools, in `propose` and again in the executor), since it is model-supplied and
lands in a URL path. In the read tools a bad name comes back as an ordinary
error for the model to read, not a logged tool crash. They cannot act on the
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
see its own runtime state (`system_status` — cycle health, proposal loops, budget
pools and throttled keys), and read the whole iot-fetcher repo's tracked source (`code_overview`,
`code_list`, `code_read`, `code_grep`, `code_log` — see
[Reading the repo](#reading-the-repo)). Nothing here mutates anything but the
brain's own `reviews/` directory and a note in the reviewed expert's inbox —
the brain never edits an expert's memory directly, it corrects through a
review, the same way any other cross-loop message travels.

**Emergent goals.** Nothing hard-codes what the agents should care about. The
constitution (`seed/constitution.md`, copied to the volume on first boot and
then owned by you) is the only fixed text; the brain rewrites `goals.md` over
time itself (`rewrite_goals`, brain-only) and proposes changes to its own
`identity.md` (`rewrite_identity`, brain-only), which take effect only when you
approve them in Slack (see [Approvals](#approvals)). Identity is the exception
because it is the one text the model writes that loads into the system prompt of
every later cycle, where injected text would carry the weight of the constitution.
An expert's persona is not rewritten by
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
│   ├── identity.md            the brain's persona; it proposes changes, you approve
│   ├── goals.md               emergent goals; it rewrites this too
│   ├── journal/YYYY-MM-DD.md  one line per cycle outcome
│   ├── facts/<name>.md        long-lived notes (max 40 before compaction)
│   ├── inbox/                 unread notes from experts and approvals
│   │   └── done/              processed notes, purged after 30 days
│   ├── reviews/<expert>.jsonl the brain's verdicts on each expert (max 50 lines each)
│   ├── sessions/<cycle-id>.jsonl[.gz]  everything one cycle did (see "Session log" below)
│   └── outbox/
│       ├── <proposal-id>.json pending/terminal proposals
│       └── slack/<ts>.json    Slack posts that failed to send, retried later
├── experts/<name>/
│   ├── persona.md
│   ├── journal/YYYY-MM-DD.md
│   ├── facts/<name>.md
│   ├── sessions/<cycle-id>.jsonl[.gz]
│   └── inbox/ (+ done/)
└── _repo/                     the code_* tools' snapshot of the public repo
    ├── current -> <sha>/      symlink, swapped atomically after each refresh
    └── <sha>/                 one extracted tarball per SHA (see below)
```

Journals keep the last 30 files — older ones are deleted after every cycle —
and facts are capped at 40 before the loop is told to compact. Both limits are
clearable rather than one-way: the agent has `delete_fact` for the fact side,
and the journal prunes itself, so the "your memory is large" hint stops firing
once it has done the work. A fact an agent overwrites or deletes keeps a revision,
see [Memory MCP](#memory-mcp). Every write goes through a temp file plus `os.replace`, so a
reader never sees a half-written file and a crash never leaves a torn ledger.

## Configuration

Copy `.env.example` to `.env`. Every variable below is read by
`ai_brain.config.load_settings`; blank means no default.

| Variable | Default | What it does |
| --- | --- | --- |
| `MEMORY_ROOT` | `/memory` | Where agent memory lives. The volume mount point. |
| `SEED_ROOT` | `/app/seed` (set in the image) | Starting constitution and personas. |
| `LLM_CHAIN` | `gemini:gemini-3.8-flash,lan:qwen3.8:27b-mlx,gemini:gemini-3.5-flash-lite,lan:qwen3-coder:30b` | `provider:model` entries tried in order until one answers. `lan:` is found by sweeping the network; `ollama:` is `OLLAMA_URL`. |
| `BRAIN_MODELS` | `gemini:gemini-3.8-flash` | `LLM_CHAIN` keys the brain may use; `all` for the whole chain. The default reserves the strongest model for the brain, which reviews how the experts reason. With gemini-3.8-flash's ~11 free requests a day, the brain runs roughly once a day, by design. Ignored (everyone shares) when the chain doesn't contain the default. |
| `EXPERT_MODELS` | every chain key not in `BRAIN_MODELS` | `LLM_CHAIN` keys the experts may use; `all` for the whole chain. A key can be listed in both. A key that names nothing in the chain, or a split that leaves a loop with no model, is a startup error. |
| `AIRBNB_ICAL_URL` | — | Secret iCal address of the Airbnb calendar (🏡 house, 📎 annex). A credential: env only, never logged or returned by a tool. Enables `airbnb_calendar` for researcher, energy and house-ops, and an hourly job that posts a Slack cleaning reminder for back-to-back changeovers. |
| `AIRBNB_REMINDER_HOUR` | `18` | Stockholm hour after which tomorrow's back-to-back changeovers are reminded about. A same-day changeover is reminded at any hour, so a restart can't swallow it. |
| `GEMINI_API_KEY` | — | Google AI Studio key. Required whenever `LLM_CHAIN` has a `gemini:` entry; the process refuses to start without it. |
| `NUM_CTX_BY_MODEL` | `lan:qwen3.8*=131072,lan:qwen3*=32768,ollama:*=16384` | Context window (tokens) requested per call, via `options.num_ctx` on Ollama's native `/api/chat` endpoint, for `ollama:` and `lan:` providers: comma-separated `pattern=N` entries, matched fnmatch-style against the `LLM_CHAIN` entry (`lan:qwen3.8:27b-mlx`) when the chain is built. First match wins; no match falls back to 32768 for `lan:` (a desktop on the LAN) and 16384 for `ollama:` (the rpi5's own in-compose service, 8GB RAM). `gemini:` entries ignore it. `N` must be 4096-262144 -- a malformed entry is a startup error. Unset or blank uses this default; `none`/`off` leaves every provider on its fallback. Ollama's own server default is 4096 and it truncates an oversized prompt silently **from the front** -- dropping the persona/system turn a cycle needs most -- so the window is always set explicitly; observed cycle prompts run ~9.7k-30k tokens. The per-request value overrides the server's own default, so keep `N` within what the host can hold in RAM. The `ollama` service in `docker-compose.yml` sets `OLLAMA_CONTEXT_LENGTH=16384` as a server-side fallback for clients that do not send `options.num_ctx`. This option is native-API-only: `/v1/chat/completions` ignores per-request `options`. When a prompt would not fit, the loop trims it first -- see [What a model sees](#what-a-model-sees). |
| `THINK_BY_MODEL` | `lan:qwen3.8*=xhigh` | Ollama's `think` field per model, for `ollama:` and `lan:` providers: `pattern=level` entries matched like `NUM_CTX_BY_MODEL`. `level` is `true`, `false`, `low`, `medium`, `high` or `xhigh`; anything else is a startup error. No match sends no `think` (the model's own default). Qwen3.8 on Ollama only knows `false`/`low`/`medium`/`xhigh` and silently runs `medium` for anything else, so use `xhigh` for its top level. Unset or blank uses this default; `none`/`off` sends no `think` anywhere. Thinking tokens count against `CYCLE_MAX_TOKENS` (`num_predict`). |
| `EXPERTS` | `energy,health,house-ops,researcher,infra` | Which expert loops to start. Unset **or blank** means all five; set a shorter list, or `none`, for brain only — see [Rollout](#rollout). |
| `BRAIN_HEARTBEAT_MIN` | `240` | Minutes between brain cycles. The brain reflects on the experts rather than watching the house, so it wakes rarely. |
| `EXPERT_HEARTBEAT_MIN` | `120` | Minutes between each expert's cycles. |
| `BRAIN_MIN_WAKE_GAP_MIN` | `30` | Least minutes from the end of one brain cycle to the start of one rung by an expert's `send_note` or the ledger. The note waits unread in the inbox until then. Notes from Filip (Slack, MCP) and from approvals still wake the brain at once. `0` turns the gate off. |
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
| `SLACK_STALE_THREAD_H` | `72` | A reply into a topic thread with no activity for this many hours is also broadcast to the DM's main view (`reply_broadcast`), so it isn't buried in an old thread. |
| `SLACK_QUIET_HOURS` | `22-07` | Stockholm hours (`start-end`, end exclusive) during which non-urgent `slack_post`s are held and sent as one morning digest. Urgent posts and replies to your own messages go out at once. `off` disables. |
| `DRY_RUN` | `0` | Exactly `"1"` means no side effect leaves the process. |
| `RPM` | `8` | Requests per minute, per model key. |
| `TPM` | `200000` | Tokens per minute, per model key. |
| `RPD` | `200` | Requests per day, per model key. |
| `LAN_SUBNETS` | *(empty)* | Subnets to sweep for the `lan:` model's host. Empty means the /24 `HA_URL` is on — see [Local models](#local-models). |
| `LAN_SCAN_MIN` | `10` | Minutes between sweeps. |
| `CALL_TIMEOUT_S` | `60` | Seconds one model call may take before the chain falls to the next provider. Not read by `lan:` providers, which set their own (below) -- a local model can legitimately need minutes, nothing like a cloud API's SLA. |
| `CYCLE_MAX_ROUNDS` | `16` | Tool rounds one cycle may take before the loop stops waiting for `end_cycle`. |
| `CYCLE_MAX_ROUNDS_BY_MODEL` | `gemini:*3.8*=32,lan:qwen3.8*=32` | Per-model override of `CYCLE_MAX_ROUNDS`: comma-separated `pattern=N` entries, matched fnmatch-style against the chain key of whichever model answered the cycle's most recent round -- the same `provider:model` shape as an `LLM_CHAIN` entry (`gemini:gemini-3.8-flash`, `lan:qwen3-coder:30b`). First match wins; no match falls back to `CYCLE_MAX_ROUNDS`. `N` must be 1-64 -- a malformed entry or an out-of-range `N` is a startup error, same as a bad `LLM_CHAIN`. Unset or blank uses this default; `none`/`off` (case-insensitive) disables per-model caps entirely. See [Effort and per-model rounds](#effort-and-per-model-rounds). |
| `CYCLE_MAX_PROMPT_TOKENS_BY_MODEL` | `lan:*=2000000,ollama:*=2000000` | Per-model override of `CYCLE_MAX_PROMPT_TOKENS`: `pattern=N` entries matched against the chain key of whichever model answered the latest round, first match wins, no match uses the flat budget. Local models cost nothing per token, so they get a much larger (still finite) budget. `N` is 0-10000000; 0 means no budget for that model. Unset or blank uses this default; `none`/`off` gives every model the flat budget. |
| `CYCLE_MAX_TOKENS` | `8000` | Output tokens per round. |
| `CYCLE_MAX_PROMPT_TOKENS` | `400000` | Per-cycle prompt-token budget across every round combined. `0` disables it. Crossing it mid-cycle is handled the same way a per-model cap drop is -- see [Effort and per-model rounds](#effort-and-per-model-rounds). |
| `GEMINI_THINKING_BUDGET` | `-1` | Gemini thinking budget per call: `-1` dynamic, a positive number caps it, `0` sends no `thinkingConfig`. A model that rejects the field is retried once without it. |
| `SESSION_RETENTION_DAYS` | `30` | Days a cycle's [session log](#session-log) is kept. `0` turns session logging off. |
| `SESSION_MAX_MB` | `200` | Size cap on one agent's session logs; the oldest go first. `0` means age is the only limit. |
| `RESUME_MAX_AGE_H` | `26` | An unfinished cycle older than this is abandoned rather than resumed. 26h because a `no_budget` stop can wait up to 25h for the Pacific-midnight reset. See [Resuming a cycle](#resuming-a-cycle). |
| `RESUME_MAX_ATTEMPTS` | `3` | How many times a resumed cycle may kill the process before it is abandoned. `0` turns resume off. |
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

**Restarts do not wake the loops.** Each loop saves the wake it chose
(`last_cycle_at` + `next_wake_s` in its `_state.json`) and, after a restart,
waits out what is left of it before its first cycle, so a deploy does not spend
the brain's handful of daily requests on a cycle that was not due. It starts at
once when there is no saved state, the wake has already passed, the last cycle
was cut off by the shutdown, or notes are waiting in its inbox -- the brain's
expert notes still sit out `BRAIN_MIN_WAKE_GAP_MIN`, Filip's and approvals'
start it immediately. The wait never exceeds the wake that was saved (a clock
that stepped back cannot make it longer), and the wake bell ends it early like
any other sleep.

To pause at any point: `touch volumes/ai-brain-memory/PAUSE`. It takes effect
at the top of the next round — so mid-cycle, not only at the next cycle — no
restart needed, and it does not lose queued work.

## What a cycle is supposed to do

A loop that wakes every hour and reads the same gauges will, left alone, write
"all systems operating normally" forever. Two things push against that:

- **The cycle instructions** (`instructions_for` in `loop.py`, shipped in the
  image) set the bar: a cycle must end with something that was not true of its
  memory before it started. Normal readings are worth learning once, as a
  baseline; after that only departures are news. Threads beat snapshots, and
  the journal is written in the agent's own voice, opinions included. The bar
  is shared by every loop; the rest is by role, because the tool surfaces
  differ. The brain's text also covers goals, introspection, `propose`, looking
  things up (including asking the researcher, since it has no web or Drive of its
  own) and answering Filip in Slack; an expert's instead says how to reach the
  brain (`send_note` with `to="brain"`) rather than describing tools it does not
  have. The constitution is shared by all six loops, so it is role-neutral: it
  says nothing about Slack, `propose` or approval, which are the brain's (the
  rules that used to be there are in the brain's instructions), and a test
  checks that the seed and the instructions name only tools the role has.
- **An angle** is handed to each loop in the user turn — "chase one anomaly",
  "build a baseline", "pick up an open thread", "read rather than measure",
  "tend your memory". It rotates hourly and is offset per loop, so five loops
  waking together do not all take the same one, and a restart does not reset
  everyone to the first angle. It is explicitly a suggestion: a loop that is
  mid-investigation is told to ignore it.

**What goes where in the prompt.** The system messages hold only text that
cannot carry instructions: the constitution, the persona, the cycle
instructions and fact names (which `safe_name` limits to `[a-z0-9_.-]`). Every
free-text thing an agent wrote is in the user turn instead, each inside its own
`<external>` fence (the same one tool results use, in `ai_brain/fence.py`, which
defuses any `<external`/`</external` in the text so it cannot close the fence):
`goals` (the brain's), `gaps`, `rejections` (the "Filip has said no to"
summary, whose targets and topics come from earlier proposals), `journal` and
one fence per inbox note. A note's fence names its sender
(`source="note-energy"`), taken from the note's header, so a body claiming to be
from Filip is still labelled as whoever really sent it. The cycle instructions
say fenced text is data, with two exceptions to "someone else wrote it": a
`note-filip` fence is Filip, and what he tells the brain is still ground truth,
and the `goals` and `gaps` fences are the agent's own agenda -- worked on, but
unable to give orders. The brain's `identity.md` is the one brain-written file
that is still in the system message (see below), and a change to it needs your
approval. The reason is the path from the researcher: a web page it reads can end
up in a note to the brain, which holds `propose` and `slack_post`, and in a system
message that text would carry the same weight as the constitution. (A `topic:` first line stays the first line
inside the fence, so thread routing and the owed-reply check are unaffected.)
The inbox is held to about 12,000 characters of full notes, each charged for its
heading and fence as well as its body (the fence is resent every round); older
notes from other loops shrink to a one-line preview past that, and Filip's never
shrink or spend the budget.

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
> without a restart. The agent rewrites `goals.md` itself over time, and
> `identity.md` changes when you approve a proposal, so what you overwrite may be
> its own words rather than yours.

## Effort and per-model rounds

`CYCLE_MAX_ROUNDS` is one flat ceiling for every cycle, but the chain can
answer the same cycle with different models round to round as it falls back
(the free-tier flash model, then the LAN box, then the rpi5's own small one --
see [Best model first](#what-it-is)). A weak fallback model spinning in circles
for 16 rounds and a strong one that could have used 30 to actually finish an
investigation are not the same problem, so `CYCLE_MAX_ROUNDS_BY_MODEL` lets the
strong ones earn more room:

```
CYCLE_MAX_ROUNDS_BY_MODEL=gemini:*3.8*=32,lan:qwen3.8*=32
```

Each entry is `pattern=N`, matched fnmatch-style (`*`/`?`/`[...]`) against the
chain key of whichever model answered the cycle's **most recent** round — the
same `provider:model` shape an `LLM_CHAIN` entry uses, e.g.
`gemini:gemini-3.8-flash` or `lan:qwen3-coder:30b` (the `lan:` provider's own
`@ host` suffix on the human-readable model string is not part of the key it
matches against). Patterns are tried in order and the first match wins; a
model that matches nothing keeps `CYCLE_MAX_ROUNDS`. `N` must be between 1 and
64 — a malformed entry (no `=`, a non-integer `N`, or `N` outside that range)
is a startup error, the same way an unparseable `LLM_CHAIN` entry is.

The cap in force is re-evaluated after every round, using whichever model just
answered — not the model the cycle started on. That cuts both ways: a cycle
that opens on a matched, strong model gets the higher cap immediately, but one
that later falls back to an unmatched, weak model is cut at the weak model's
limit from that round on, even if the strong model had already carried it past
that number. No per-model entry can push a cycle past `CYCLE_MAX_ROUNDS_HARD_CEILING`
(64) regardless of what the pattern says — a belt-and-braces ceiling on top of
load-time validation. Unset or blank falls back to the documented default
above; `none` or `off` (case-insensitive) turns per-model caps off entirely,
leaving every cycle governed by the flat `CYCLE_MAX_ROUNDS`.

`WRAP_UP_ROUNDS_BEFORE_CAP` (2) rounds before the effective cap, the loop
appends one wrap-up nudge to the conversation ("You have N round(s) left:
write what you found ... and call `end_cycle` now", `N` singular/plural to
match the real count), once per cycle — enough runway for the model to write a
fact or journal line and call `end_cycle` cleanly rather than being cut off by
`max_rounds` mid-thought. A cap that *drops* mid-cycle (a fallback to a weaker
model, or the token budget below) can otherwise land inside or past that
window before the nudge has ever been sent — worst case, the fallback lands
so late that the cycle would end on the very round the cap dropped, with the
model never having seen a nudge at all. When that happens the drop is rescued
once: the effective cap for that round is raised to `rounds +
WRAP_UP_ROUNDS_BEFORE_CAP` (never above the dropped cap if that is even
higher, and never above `CYCLE_MAX_ROUNDS_HARD_CEILING`), just enough for the
nudge to land and for the model to get one more round to act on it. This only
ever happens once per cycle, the same as the nudge itself.

The quota clamps the cap too. A model with 11 requests a day (see
[Quota](#quota)) cannot play a 32-round cycle, and without a clamp the nudge
would wait for round 30 while the cycle met `ChainExhausted` at round 12 with
nothing written down. After every round the loop asks the ledger how many
rounds the answering key has left (`Ledger.rounds_left`) and, when no
*other* provider this loop may use is dialable with quota left, caps the cycle
at `rounds + remaining`. "Left" is the least of every budget `can_spend`
enforces -- the daily requests, the daily tokens, the expert floor on both for
an expert on a key the brain shares, and a USD pool's month, day and expert
share -- and the two are one piece of code (`Ledger._budget_refusal`), so they
cannot disagree about where a budget runs out. Tokens and dollars are counted
per round, priced at what the round just played cost (the conversation is
resent whole each round, so the next costs at least as much; the nudge's two
spare rounds absorb the difference); the per-minute windows are not counted,
since they reopen by themselves. The same nudge then fires two rounds before that
point, and a cycle that still does not call `end_cycle` ends `max_rounds` with
a summary naming the quota. A cycle that can fall through to another model
(`BRAIN_MODELS=all`, say, or an expert with a `lan:` host up) is not clamped,
and neither is an unmetered `lan:` key. The quota is a hard limit, so unlike a
cap drop it is never rescued upward by the wrap-up floor.

The rounds actually used and the cap in force are both in the journal line
(`rounds=12/32`) and the trace/API (`cap`), so `journal.md` and Grafana both
show whether a cycle is running out of room or ending early.

Every per-model setting on this page comes back through one lookup,
`ModelProfiles.for_key` in `profiles.py`. `NUM_CTX_BY_MODEL`, `THINK_BY_MODEL`,
`CYCLE_MAX_ROUNDS_BY_MODEL`, `CYCLE_MAX_PROMPT_TOKENS_BY_MODEL` and the measured
daily limit in `KEY_RPD_OVERRIDES` are read from there; the variables themselves
are unchanged. The round-cap rules are a pure function in `caps.py`.

`CYCLE_MAX_PROMPT_TOKENS` (default `400000`, `0` disables it) is the same idea
applied to token spend instead of round count: a cycle that keeps pulling in
large tool results round after round without ever calling `end_cycle` can burn
through a huge prompt long before it hits any round cap. Crossing the budget
is handled exactly like a cap drop — the wrap-up nudge fires if it has not
already, at most `WRAP_UP_ROUNDS_BEFORE_CAP` more rounds are allowed, and the
cycle then stops with status `max_rounds` and a summary naming the token
budget rather than the round cap. Once the budget has stopped a cycle the stop
is sticky for the rest of it: a later round answered by a model whose own
`CYCLE_MAX_ROUNDS_BY_MODEL` entry would otherwise raise the cap cannot undo
it.

## Approvals

The brain never acts on the house directly. It calls `propose`, which writes
`outbox/<id>.json` with status `pending` and posts the request to your Slack
DM. You react on that message:

- ✅ (`:white_check_mark:`) — the executor runs. Currently `sonos_say`
  (speaks in `SONOS_ROOM`), `ha_todo_add` (appends to `HA_TODO_LIST`) and
  `ha_service` (one Home Assistant service call on one entity).
- ❌ (`:x:`) — rejected, nothing happens.
- No reaction for 24 hours — expired.

**Identity rewrites.** `rewrite_identity` is brain-only and writes nothing: it
files a proposal of kind `rewrite_identity` holding the whole proposed text, posts a
card to Slack and returns "pending approval" with the proposal id. ✅ applies it
through `MemoryDir.rewrite_identity`, so the previous text is kept in the revision
history (`history kind=persona` on the MCP) and the file's emoji frontmatter stays.
❌ and expiry leave the file untouched, and the outcome reaches the brain's inbox
like any other. Your own edits through the MCP's `write_persona` stay direct, and
`rewrite_goals` is unchanged. The `propose` tool does not offer this kind.

- **The card shows what you approve.** After the provenance warning (first, as on
  every card) comes a diff against the current identity, in code blocks, then the
  instructions to react. The proposed text is escaped (`& < >`, so a `<!channel>`
  is inert) and its backticks cannot close the code block, so it cannot pose as the
  card's own wording. The brain's one-line reason is quoted. A body is refused if
  it holds invisible or control characters, since the card could not show them.
  If the diff does not fit Slack's limits (five 3,000-character blocks, which a
  complete rewrite of a maximum-size identity fits in), the card says so, with how
  many lines of how many it shows, and that the full text is in
  `brain/outbox/<id>.json` under `payload.body`. After you answer, the card
  becomes the verdict plus the text's hash and size.
- **Bounds.** 200 to 6,000 characters, no empty body, no `---` frontmatter block,
  no whole sentences of the cycle instructions pasted in, and secret-shaped text is
  masked before it is hashed, shown or stored. One identity request waits at a
  time. Your MCP edits have no limit.
- **A stale proposal is not applied.** The proposal records the hash of the
  identity file it was based on. If the file changed before you approved, it ends
  `failed` with a note saying so and the newer text is untouched; the brain can
  propose again against it.
- **Rejection memory** keys on a hash of the proposed text, not on "identity": the
  same text stays refused for `REJECTION_MEMORY_DAYS`, different text may be asked.
  In "Filip has said no to" it shows as `identity sha256:… (N chars)`, never the
  text.
- **Restarts.** A restart between ✅ and the write is not "outcome unknown" for
  this kind: the write is one atomic replace, so startup checks whether the file
  holds the proposed text and records `executed` or `failed` accordingly. A resumed
  cycle never calls the tool again on its own account: it is not in
  `READ_ONLY_TOOLS`, so a call without a result is reported as "outcome unknown".
  `DRY_RUN=1` writes nothing, like the other executors.

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

A rejection *is* tracked, at the code level. Every one of the brain's own
cycles (not an expert's — only the brain can call `propose`) gets a "Filip
has said no to" section in its opening context, grouped by kind + target (a
service+entity for `ha_service`, a todo item, a container) + topic — the
count, the date of the last rejection, and its reason if one is on record.
`propose` itself refuses a new proposal outright when its kind + target
matches a rejection from the last `REJECTION_MEMORY_DAYS` days (default 30):
the error names the earlier rejection and tells the model to ask in Slack
instead if it genuinely has new evidence, rather than silently re-asking. For
`ha_service`, the target ignores bare numeric arguments (a setpoint, a
brightness level) so a different value on an otherwise-identical, already-
rejected call is still refused — but a different service on the same entity
(`light.turn_on` after a rejected `light.turn_off`) is a different ask.

Two guarantees are worth knowing when reading logs:

- **Exactly once.** Slack redelivers, and a human can double-tap. The status
  moves out of `pending` on disk *before* the executor is awaited, so the
  second delivery finds a non-pending file and stops. A process killed
  mid-execution leaves a proposal stuck at `executing` — deliberately, since
  the safe failure is "did not retry", not "spoke twice".
- **Quiet hours.** `sonos_say` refuses between 22:00 and 07:00 Europe/Stockholm
  and records `blocked_quiet_hours`.

## Provenance

A fence tells a model not to obey a web page. It does nothing about the page having
shaped what the model then writes, and a fact reads back later as this system's own
words. So a cycle tracks what outside text it has read, and what it writes after that
says so.

- **What counts:** anything a tool returned through `wrap_external` (`web`,
  `google-drive`, `home-assistant`, `docker`, and `session-history` for a hit from a
  loop's past cycles, whose logs hold what those cycles read), and a tag it picks up by reading a tagged
  note or fact, so the tag follows a claim from one loop to the next. Filip's own notes
  and MCP edits carry none.
- **Where it shows:** a fact keeps it in `facts/_meta.json`, and `read_fact` and
  `read_expert` return it as `written_after_reading`. A note keeps it in its header, and
  the inbox renders it on the line above the note's fence: `from: energy (...), after
  reading external text (web)`. The fenced body cannot forge or remove that line. A
  proposal keeps it in its file and shows it on the Slack card, above the buttons.
- **A rewrite replaces the tag.** A cycle that reads a tagged fact is carrying the tag
  already, so it only goes away when a rewrite happened with no outside text in view.
  A rename or a restore is not a rewrite: the words keep the tag they had.
- **Advisory only.** Nothing is ever refused because of it. It says the writer had read
  this, not that what it wrote came from it.

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
  included. Both tools fence everything an expert wrote -- persona, journal,
  fact titles and bodies, gaps, and the brain's own findings about it -- as
  external text (`source="journal-energy"` and so on), since an expert may have
  copied it from a web page and the brain is the loop that can `propose` and
  post. Fact titles come as one `fact_titles` block, gaps as one `gaps` block.
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

The chain puts the strongest model first and interleaves cloud and house hardware by quality, so a weak cloud model never pre-empts a better local one:

```
gemini:gemini-3.8-flash → lan:qwen3.8:27b-mlx → gemini:gemini-3.5-flash-lite → lan:qwen3-coder:30b
```

Each Gemini key answers until the ledger says its quota is gone (or three 429s
park it), and the next entry answers from then until the day
rolls over. `ollama:` (the rpi5's own service) is supported but not in the default chain: a 3B model on the Pi's CPU mostly timed out and could not tool-call reliably. **`lan:` is a model on whatever machine in
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
- **A per-minute window is waited out, not fatal.** `RPM` and `TPM` apply to
  `gemini-3.8-flash` like any metered key (8 requests a minute by default), and
  a brain with that one key can play nine fast rounds inside a minute. When the
  only thing refusing a round is such a window -- no call failed, and the chain
  has nothing else to fall to -- `ChainExhausted` is marked `windowed` and the
  loop sleeps until the oldest request leaves the window (plus half a second)
  and asks again, rather than ending the cycle `no_budget` and discarding the
  transcript. The wait is outside the per-call timeout, sliced into 2-second
  naps so a `PAUSE` file or a shutdown is noticed at once, and at most 10
  minutes per cycle; past that, or for anything that is not a window (a
  spent day, a parked key, a failing provider), the cycle ends as before.
  Because the wait is only a minute, `rounds_left` still ignores `rpm`/`tpm`.
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
  `EXPERTS` if you would rather it stayed on the strong model all day. A round
  on `gemini-3.8-flash` or the LAN `qwen3.8` model is one request regardless of
  how generous its own `CYCLE_MAX_ROUNDS_BY_MODEL` cap is — see
  [Effort and per-model rounds](#effort-and-per-model-rounds) — so a higher
  per-model cap raises how much a *single* cycle can do on that model, not how
  many requests the day's `RPD` allows.
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

Whether the cycle guards earn their keep is a question for the session logs:

```sh
make report                       # ../volumes/ai-brain-memory; DAYS=7 for a window
sudo docker exec ai-brain /app/.venv/bin/python -m ai_brain.report --days 7 [--json]
```

Per model: cycles, rounds answered, rounds per cycle, how many cycles were cut off at
their cap without `end_cycle`, how often the wrap-up nudge fired and `end_cycle`
followed, junk-fact refusals, context trims and resumes. Then each angle: how often it
was offered and, where a tool gives it away, a proxy for how often it was taken. A model
never says which angle it followed, so that second number can err both ways: a tool
called for another reason counts, and an angle followed without needing its tool does
not. The report only reads, and it changes nothing. No guard is removed on this data yet: it is here so that one can be,
with evidence, later.

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
[session log](#session-log) is the history, this is the live view. Each round's `tool_results`
entries carry a `stats` object alongside `result_preview` -- `{"chars": N}`
always, plus `ok` (false when the parsed result has an `"error"` key),
`lines` (a string `body`'s line count), `series` and `hits` (list lengths),
whichever of those the tool's own result actually has. It is computed from
the tool's full, untruncated result, so it stays accurate even once
`result_preview` itself has been cut off. From the box:

```sh
curl -s http://localhost:8091/api/agents/brain/trace
```

## Session log

The journal says one line per cycle and the trace forgets everything at the next
one. In between, each cycle appends what it did to
`<agent>/sessions/<cycle-id>.jsonl`, one JSON object per line, while it runs:

| Event | Holds |
| --- | --- |
| `opening` | The opening messages, the inbox files they rendered, the angle. |
| `reply` | One model turn, with what is needed to rebuild it: text, thinking, tool calls, signatures, provider blocks, the model and chain key, token counts. |
| `tool_call` | A tool is about to run. Its arguments are in the `reply` above it. |
| `tool_result` | What the tool returned, before the loop's `MAX_TOOL_RESULT_CHARS` cut. |
| `memory` | A write to a fact, goals or a gap, with the body it replaced. (An approved identity rewrite happens in the Slack handler, not in a cycle, so it is not here; the revision trail has it.) |
| `message` | A turn the loop itself added, such as the wrap-up nudge. |
| `context_fit` | A call's prompt was trimmed: for which model, which results, and how many turns lost their reasoning. |
| `cycle_end` | Status, rounds, cap, summary, token totals, and how many events failed to land. |

- **It never breaks a cycle.** A failed write is logged once and forgotten. Each
  event has a `seq`, so a missing one shows as a gap, and the first failure also
  renames the file to `.jsonl.holes`, since a hole at the end would otherwise look
  like a log that simply stopped.
- **SIGKILL loses at most the line being written:** each event is one unbuffered
  `write`. There is no `fsync` per event, which would cost a journal commit on
  the SD card for every tool call, so a power cut can lose the last few seconds.
- **Everything is redacted first**, except the provider-signed blobs
  (`thought_signature`, a thinking block's `signature`) which are ciphertext. A tool
  argument is masked by its name too, along with every string nested under it.
  Redaction goes by shape and name, so a value under 8 characters in free text
  stays. A tool result is capped at 512k chars and a cycle at 64 MB.
- **A finished log is gzipped and pruned:** by age (`SESSION_RETENTION_DAYS`) and
  then, oldest first, by size (`SESSION_MAX_MB`). A log a crash left behind is
  picked up by the next cycle to end.
- **An unfinished log stays uncompressed** until it is resumed or abandoned.
- **Files are `0600` and not served over HTTP.** The API has no authentication,
  so its trace keeps carrying previews; the trace's `cycle_id` names the file.

```sh
sudo zcat volumes/ai-brain-memory/brain/sessions/20261103-101500-a1b2.jsonl.gz | jq -c '[.seq, .type, .name]'
```

An agent reads its own log with `search_history`: a word or phrase, newest first,
in short snippets. It sees only its own loop's history. It is bounded in hits,
snippet length and how much it decompresses per call, and its snippets come back
fenced as external text, since a logged result can be a web page.

## What a model sees

Storage and context are kept apart. The session log holds everything, and
`ai_brain.context.fit_context` decides what fits in front of one model on one
call. It returns a new list and never touches the loop's own `messages`, so a
result one small window could not hold is still there when a larger one answers.

The policy runs per provider, not per round, since only the chain's walk knows who
ends up answering and a fallback's window can be a fraction of the first choice's.
It applies to providers with a window this process asks for (`ollama:`, `lan:`).
`gemini:` and `anthropic:` have their own, far above `CYCLE_MAX_PROMPT_TOKENS`.

The budget is `NUM_CTX_BY_MODEL`, less the reply (`CYCLE_MAX_TOKENS`), the tool list
and a fixed overhead. Past it, oldest first: reasoning echoed on earlier turns, then
tool results, each replaced by a stub naming where the original is. The system turns
and the last turn are never touched. Each time, a `context_fit` event in the log
records what was left out.

Every cut is recoverable within the cycle. A trimmed result, or one past
`MAX_TOOL_RESULT_CHARS`, says which `read_cycle_events(from, to)` call brings it back.
That tool reads the current cycle's own log and nothing else (it takes no cycle id),
pages through a long result with `next`, and fences what it returns as external.

Providers keep one last-resort guard against Ollama silently cutting the *front* off
a prompt, which is where the persona lives. On the loop's path it fires only when
the policy could not get the prompt under budget, as when the opening turns alone
overflow a small window.

## Resuming a cycle

A cycle that stops without finishing is picked back up from its log by the next
`run_cycle` of that loop, instead of being thrown away. That covers a crash or a
`SIGKILL` (no `cycle_end`), a restart (`cancelled`), `no_budget` and a mid-cycle
`PAUSE`. `timeout`, `error` and `max_rounds` are over: they have archived their
inbox, and carrying on from them is how one bad reply turns into a cycle that
never ends. A cycle that stopped cleanly before its first reply is dropped instead,
since a fresh one loses nothing and sees a newer inbox. One that *died* before its
first reply is resumed, so that repeat deaths can be counted. A log marked
`.jsonl.holes` is never resumed: it cannot be trusted to say what happened, so it is
abandoned like any other with holes and its notes go to a fresh cycle.

- **Side effects are never replayed, and neither is anything else.** The harness
  runs no tool on resume. A call on record with no result gets a synthetic one:
  `outcome unknown ... check before retrying` for a call that started, `no result
  ... call it again` for a call in `READ_ONLY_TOOLS`, and `not run` for one that
  never started. `READ_ONLY_TOOLS` is an allowlist, so a tool nobody thought about
  counts as a write. Those results go into the log, so a second resume reads the
  same thing.
- **The model is told how long it has been**, in a user turn, and to re-read
  anything it is about to rely on.
- **State comes back from the log:** rounds, token counts, the round cap, the
  sticky token-budget stop, whether the nudge fired, who is still owed a reply. A
  resumed cycle gets no fresh rounds. An uncertain `slack_post` counts as
  answered, so `end_cycle` cannot talk the model into posting it twice. The worst
  case is the spinner the watchdog already turns red.
- **The inbox stays exactly-once.** The notes a cycle rendered stay unread until
  it finishes. Notes that arrive while it is down are shown in one more user turn
  and logged, so a later resume knows about them. If no model will be asked anything
  more, they stay unread and the loop wakes at once to read them. A post only counts
  as answering what had been shown before it.
- **Model changes are safe.** The chain may answer with another model on resume,
  and another model's turns are replayed as text, as they are mid-cycle. A turn
  redaction changed is replayed as text too, because its signature no longer
  matches. If a provider still refuses the history with a 400 on the first call,
  every earlier turn is replayed as text, once, before the cycle gives up.
- **A cycle whose work was done is only closed.** If the log shows the model
  already called `end_cycle` or stopped calling tools, no further call is made.
- **Abandoned, with a journal line:** when the cycle is older than
  `RESUME_MAX_AGE_H`, when its log has holes (a missing event may be a side effect
  nobody can see), or when it has killed the process `RESUME_MAX_ATTEMPTS` more
  times. In the first two cases its notes stay unread for the next cycle, except a
  note from Filip the log shows was answered, which is archived so that a fresh cycle
  does not answer it twice. Notes from other loops can be read twice. A resume that
  merely found no budget again costs no attempt. The age cap bounds those.
- **A death never costs a loop its notes.** A death is the whole process going, it is
  counted against every loop that was mid-resume, and nothing on record says whose
  fault it was. So past `RESUME_MAX_ATTEMPTS` the cycle is abandoned, with status
  `abandoned`, and its notes stay unread. What bounds a note that really does kill the
  process is time: no fresh cycle starts then, and the loop waits a heartbeat, across
  restarts too. That is at most `RESUME_MAX_ATTEMPTS` + 1 deaths per heartbeat, for as
  long as it lasts, and each one leaves a journal line. A new note that arrives while
  the process is up still wakes the loop as usual.
- **Filip does not sit out that wait.** A note of his that is already waiting gets one
  fresh cycle at once instead, recorded in `_state.json` before it starts. Each note
  gets this once, so one that really does kill the process costs one more run of
  deaths and then waits a heartbeat like any other.

## Memory MCP

A streamable-HTTP MCP server on `:8092/mcp` (`MCP_PORT`, `0` disables) for a
coding agent to read and refactor the agents' memory in bulk: facts, journals,
gaps, personas (the "soul"), the brain's goals and the constitution. It runs
in-process, so writes go through the same `MemoryDir` as the loops (atomic,
redacted) and land on each agent's next cycle.

Without `MCP_TOKEN` it is read-only and open, like the HTTP API. With it, every
request needs `Authorization: Bearer <token>` and the write tools appear. Every
overwrite or delete of a fact keeps the previous body, whether an outside edit or
the agent's own cycle did it, as does every outside edit of a persona, goals or
the constitution. `MAX_REVISIONS` (20) bounds each fact, and a deleted fact's
revisions go 90 days after its last one.

`history` reads them back, with `by` saying who replaced each. `history` with
`kind=deleted` names facts that no longer exist, and `restore_fact` (write tools
only) puts a fact back to any revision. It keeps the body it replaces, so a restore
can be undone too.

```sh
claude mcp add --transport http ai-brain http://192.168.68.87:8092/mcp \
  --header "Authorization: Bearer $AI_BRAIN_MCP_TOKEN"
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
