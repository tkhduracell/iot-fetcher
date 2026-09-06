# ai-brain — Design

Date: 2026-09-06
Branch: `cc/ai-brain-component-plan-9d3020`

## Problem

The stack has plenty of data (VictoriaMetrics, Home Assistant, Drive documents) and a
chat assistant that only thinks when spoken to. Nothing observes the household on its
own, forms opinions over time, and brings them to Filip. `ai-brain` is a small,
always-on agent, in the spirit of NemoClaw agents but scaled to one Raspberry Pi and
the Gemini free tier, that does exactly that.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Purpose | Fully emergent. No mission from Filip; it observes and picks its own projects. |
| Acting on the world | Allowlisted actions only, every one gated on a Slack ✅ from Filip. |
| Integrations (v1) | VictoriaMetrics, Home Assistant, Sonos say, gdrive-rag, web search/fetch, Slack. |
| Slack | Two-way, Socket Mode, registered as a Slack **agent** with sessions. |
| Memory | Markdown files plus a daily journal on a bind-mounted volume. |
| Sub-experts | Separate agent loops (energy, health, house-ops, researcher), each with its own memory dir. |
| Cadence | Fixed heartbeat plus event wakeups; the loop may adjust its own next wake within bounds. |
| Quota | Central ledger, brain has priority, experts starve first. |
| Models | `gemini-3.8-flash` then `gemini-3.5-flash-lite`, whichever is available; provider chain designed so Ollama cloud can be appended later. |
| Seed | Minimal constitution (~10 lines), everything else self-written. |
| Runtime | Python 3.12, asyncio, one container. |

## Architecture

One container `ai-brain`, one volume `/memory`.

```
supervisor
 ├─ ledger          quota accounting, persisted to /memory/_ledger.json
 ├─ llm             ProviderChain: gemini → gemini-lite → (future) ollama
 ├─ slack_io        Bolt Socket Mode; inbound → /memory/brain/inbox, outbound API
 ├─ brain loop      /memory/brain/
 ├─ energy loop     /memory/experts/energy/
 ├─ health loop     /memory/experts/health/
 ├─ house-ops loop  /memory/experts/house-ops/
 └─ researcher loop /memory/experts/researcher/
```

- Every loop is one class `AgentLoop(name, persona, tools, heartbeat)` parameterised
  by persona and tool subset.
- Loops never call each other in-process. They communicate only by writing markdown
  notes into another loop's `inbox/`. Slack is one more writer to the brain's inbox
  and one more reader of the brain's outbox.
- Compose: service `ai-brain` on the default network, `depends_on` `database-auth`,
  `gdrive-rag`, `sonos-http-api`; `env_file: ./ai-brain/.env` in the local override;
  named volume `ai-brain-memory`, mapped to `./volumes/ai-brain-memory` locally.
- CI: `.github/workflows/build-ai-brain.yml`, copied from `build-fetcher-core.yml`
  (arm64 runner, push to the Artifact Registry as `ai-brain:latest`).

## Memory layout

```
/memory/
  constitution.md          seed, read-only to agents, edited by Filip
  PAUSE                    optional; presence pauses all loops
  _ledger.json             quota state
  brain/
    identity.md            who it thinks it is; brain rewrites freely
    goals.md               current self-chosen purposes and why
    journal/YYYY-MM-DD.md  append-only, one file per day
    facts/*.md             durable learned facts, one topic per file
    inbox/*.md             unread notes (from Slack, from experts)
    inbox/done/            processed notes, kept 30 days
    outbox/*.md            proposals awaiting approval
    outbox/slack/          Slack messages queued while the socket is down
    sessions.json          Slack thread_ts ↔ topic map
  experts/<name>/
    persona.md             seeded by us; expert may append, not delete
    journal/, facts/, inbox/, inbox/done/
```

**Read path per cycle**: `constitution.md`, `identity.md` (brain) or `persona.md`
(expert), `goals.md` (brain), today's and yesterday's journal, every unread inbox note,
and a listing of `facts/` names. Fact bodies load on demand through `read_fact`, so the
context stays under the free-tier TPM cap.

**Write path**: the model only has `append_journal`, `write_fact(name, body)`,
`rewrite_goals`, `rewrite_identity`, `send_note(to, body)`. No free-form filesystem
access. Writes are whole-file replaces via temp file + rename, or appends, so a crash
mid-cycle leaves valid markdown.

**Compaction**: when `facts/` exceeds 40 files or the journal exceeds 30 days, the loop
gets one "consolidate" turn asking it to merge and prune. Triggered by size, not
schedule.

The directory is bind-mounted on rpi5, so `cat`, `grep` and a `git init` inside the
volume are the inspection and rollback tools.

## Loops

One cycle of any loop:

1. Wait for heartbeat (brain 30 min, experts 2 h, both configurable) or a wakeup event
   (new inbox note).
2. Ask the ledger for a slot. Denied → sleep until the ledger's retry time.
3. Build context, call the provider chain with function-calling tools, at most 8 tool
   rounds per cycle, 60 s per model call.
4. The model ends the cycle by calling `end_cycle(next_wake_minutes)`, clamped to
   `[heartbeat/2, 12 h]`.
5. Append a journal line naming the model that answered, move inbox notes to `done/`.

**Wakeups**: `slack_io` and `send_note` both drop a file in a loop's inbox and set that
loop's `asyncio.Event`. An early wake still goes through the ledger.

**Crash safety**: each cycle is wrapped in try/except that logs the traceback, appends
an `[error]` journal line and sleeps one heartbeat. The supervisor restarts any loop
whose task died.

**Observability**: the fetchers taught us a dead job looks healthy from outside. The
supervisor therefore writes `ai_brain_cycle_total{loop,status}`,
`ai_brain_ledger_remaining{model}` and `ai_brain_loop_last_cycle_seconds{loop}` to
VictoriaMetrics through the same Influx line-protocol write the Python fetchers use, so
a stuck loop is a Grafana panel, not a surprise.

## Quota ledger

`ledger.py`, one asyncio object, persisted to `_ledger.json` on every change.

- One bucket per provider-chain entry (Google quotas are per model): `RPM`, `TPM`,
  `RPD` from env with conservative defaults, reset at midnight Pacific.
- Counts requests and tokens from each response's `usageMetadata`.
- **Priority**: the brain may spend the whole remaining daily budget; experts may only
  spend while remaining daily budget is above 40%.
- **429 / RESOURCE_EXHAUSTED is authoritative.** Honour `retryDelay` if present, else
  exponential backoff from 1 min capped at 1 h. Three consecutive 429s on a bucket mark
  it exhausted until reset. The configured RPD is a soft guess; Google's answer wins.
- On reset: zero counters, wake all loops, drop a note "new day, budget restored" in the
  brain's inbox.
- Corrupt ledger file on start: zero counters but assume 50% of the day's budget already
  spent.

## Model provider chain

`llm.py` exposes `complete(messages, tools, max_tokens) → (reply, usage)`. Behind it a
`ProviderChain` built from env:

```
LLM_CHAIN=gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite
```

Adding Ollama cloud later means appending `ollama:<model>` and implementing one
`OllamaProvider` with `complete`, `is_available`, `to_provider_tools`.

Rules:

| Signal | Action |
|---|---|
| 404 / model not found | Disable entry 24 h, log once, next entry. |
| 429 | Cool down entry per ledger, next entry immediately. |
| 5xx / timeout | Retry once on same entry, then next entry. |
| All entries exhausted | Loop sleeps until earliest cooldown expiry or daily reset. |

Tool definitions live once in a provider-neutral dict and are converted per provider, so
agents never change when a provider is added.

## Tools, actions and approvals

**Read tools** (no approval, only rate limits):

| Tool | Loops | Backend |
|---|---|---|
| `vm_query(promql, range?)` | brain, energy, health | VM via `database-auth:8427`, bearer token |
| `vm_metrics(pattern)` | same | `/api/v1/label/__name__/values` |
| `ha_state(entity_or_area)` | brain, house-ops | HA REST at `192.168.68.87:8123` |
| `drive_search(query)` | brain, researcher | gdrive-rag `:8090/query` |
| `web_search(query)`, `web_fetch(url)` | brain, researcher | Brave API; fetch is text-only, 20 kB cap |
| `read_fact`, `list_facts` | all | memory |

**Memory write tools** (no approval): `append_journal`, `write_fact`, `rewrite_goals`,
`rewrite_identity`, `send_note`.

**Slack tools** (brain only, no approval, capped at 20 messages/h):
`slack_post(topic, text)` opens or continues a titled agent-session thread;
`slack_close(topic)` closes it.

**World actions** (brain only, always via approval):

- `propose(kind, payload, reason)` with `kind ∈ {sonos_say, ha_todo_add}`. Writes
  `outbox/<id>.md` and posts a proposal message in the topic's Slack thread with payload
  and reason.
- ✅ reaction from Filip executes it once; ❌ or 24 h timeout rejects it. Either outcome
  becomes a note in the brain's inbox, so it learns what is accepted.
- Executors: `sonos_say` → `sonos-http-api /say/<room>/<text>` with hard quiet hours
  22:00–07:00 enforced in code even after approval; `ha_todo_add` → HA `todo.add_item`
  on one configured list.
- Experts cannot propose. They send a note to the brain, which decides. One gate.

**Slack inbound → inbox notes**: DM text, thread replies (tagged with the topic), Stop
button (`agent_session_stopped` → note "Filip stopped <topic>", session closed),
reactions on proposals.

## Slack integration

Scope: registered as a Slack agent, Socket Mode, one DM with Filip.

Used:
- App manifest checked in as `ai-brain/slack-manifest.yml`: Agents enabled, scopes
  `assistant:write`, `chat:write`, `im:history`, `reactions:read`; events `message.im`,
  `agent_session_stopped`, `agent_session_title_changed`, `app_home_opened`.
- Proactive titled threads: `chat.postMessage` then `agents.sessions.rename` with the
  returned `ts`, so each brain topic is its own agent session in the Activity rail.
- `agents.sessions.setStatus` `processing` while a loop is working on that topic
  (re-sent before the one-hour timeout), `active` afterwards, `closed` on
  `slack_close` or Stop.
- Suggested prompts derived from `goals.md` ("What are you working on?", "Why did you
  say that?").

Skipped in v1: streaming, feedback buttons, task-list plan mode, App Home content,
channel mentions.

All logic stays in Python. `slack_io.py` is a thin adapter: Bolt handlers write inbox
notes; a small outbound API wraps the four Slack calls above. Whether Socket Mode
delivers agent-session events is verified in the first implementation task; the
fallback is the legacy `assistant_thread_*` events, which Bolt's compatibility bridge
supports.

## Error handling and safety

| Failure | Response |
|---|---|
| Tool backend down | Tool returns `{"error": ...}` to the model, never raises. Journal gets `[tool-error]`. |
| Slack socket drops | Bolt reconnects; outbound retries 3× with backoff, then queues in `outbox/slack/` and flushes on reconnect. Loops keep running. |
| Malformed tool call | Error returned to the model, counts toward the 8-round cap. |
| Tool outside the loop's allowlist | Refused by the dispatcher, logged `[policy]`. |
| Write outside the loop's dir | Impossible: tools take names, not paths, bound to the loop's dir. |
| Runaway cycle | 8 rounds, 60 s per call, cycle aborts with `[timeout]`. |
| Constitution missing | Refuse to start. |

**Prompt injection**: web pages, Drive documents and HA entity names are untrusted. All
external text is wrapped as `<external source="...">` and every persona states such
content is data, not instruction. World actions already require ✅, so the worst case is
wasted quota or a spammed thread, and the Slack cap bounds the spam.

**Kill switches**: Stop button per topic; `touch /memory/PAUSE` pauses every loop within
one heartbeat and posts a note. Removing the file resumes.

**Secrets**: `GEMINI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `INFLUX_TOKEN`,
`HA_TOKEN`, `BRAVE_API_KEY` in `ai-brain/.env`. Never in `/memory`; no tool reads env.

## Testing

**Unit** (pytest, no network):
- `ledger`: priority rule, 429 cooldown, three-strikes exhaustion, Pacific reset,
  corrupt-file recovery.
- `ProviderChain`: 404 disables, 429 falls through, all-exhausted sleeps, tool-schema
  conversion round-trips.
- `memory`: context assembly, atomic writes, compaction trigger, path confinement.
- `AgentLoop`: fake provider with scripted tool calls; journal lines, inbox → done,
  `end_cycle` clamping, round cap, allowlist refusal.
- `approvals`: propose → outbox → ✅ executes once, ❌ rejects, timeout rejects, quiet
  hours block after ✅.
- `slack_io`: Bolt test client; events become correctly tagged inbox notes.

**Integration** (local, real backends, fake model): `make dev` runs the container
against rpi5's VM, HA and gdrive-rag with `LLM_CHAIN=fake:scripted` to prove every tool
round-trips. `DRY_RUN=1` makes the Sonos executor log instead of speak.

## Rollout

1. Create the Slack app from the checked-in manifest, Socket Mode, install to the
   workspace.
2. Deploy with `PAUSE` present and `EXPERTS=` empty; brain heartbeat 30 min. Watch two
   days of journal and the ledger panel in Grafana.
3. Enable experts one at a time via `EXPERTS=energy,researcher,...`.
4. Remove `PAUSE`.

## Success criteria

- The brain runs a week without a manual restart.
- Google quota is only ever exceeded through authoritative 429s, never silently.
- Every world action has a matching ✅ in Slack.
- `goals.md` contains purposes Filip did not write.

## Out of scope (v1)

Ollama provider, Slack streaming, feedback buttons, App Home, channel mentions, any HA
write other than `todo.add_item`, embedding-based memory recall, multi-user.
