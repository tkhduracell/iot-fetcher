import gzip
import json

import pytest
from sessions_helpers import build_loop, call, end, reply

from ai_brain.caps import WRAP_UP_ROUNDS_BEFORE_CAP, CapState, after_reply, nudge_due
from ai_brain.config import CYCLE_MAX_ROUNDS_HARD_CEILING, load_settings
from ai_brain.ledger import Ledger
from ai_brain.llm import ProviderChain, limits_from_settings
from ai_brain.loop import BRAIN_ANGLES, EXPERT_ANGLES
from ai_brain.profiles import (
    DEFAULT_NUM_CTX,
    KEY_RPD_OVERRIDES,
    LAN_DEFAULT_NUM_CTX,
    ModelProfile,
    ModelProfiles,
)
from ai_brain.report import (
    ANGLE_SIGNALS,
    build_report,
    load_cycles,
    main,
    render,
    summarize,
)
from ai_brain.sessions import read_events, session_files


@pytest.fixture
def wall():
    state = {"t": 1_800_000_000.0}

    def _now() -> float:
        return state["t"]

    _now.state = state
    return _now


# -- ModelProfile: every old setting, same answer -------------------------


def _profiles(env: dict | None = None) -> ModelProfiles:
    return ModelProfiles.from_settings(load_settings(env or {}))


@pytest.mark.parametrize(
    ("key", "num_ctx", "think", "rounds", "budget"),
    [
        ("lan:qwen3.8:27b-mlx", 131072, "xhigh", 32, 2_000_000),
        ("lan:qwen3-coder:30b", 32768, None, 16, 2_000_000),
        ("lan:other:7b", LAN_DEFAULT_NUM_CTX, None, 16, 2_000_000),
        ("ollama:llama3:3b", DEFAULT_NUM_CTX, None, 16, 2_000_000),
        ("gemini:gemini-3.8-flash", None, None, 32, 400_000),
        ("gemini:gemini-3.5-flash-lite", None, None, 16, 400_000),
        ("anthropic:claude-opus-x", None, "medium", 32, 200_000),
        ("anthropic:claude-haiku-x", None, None, 16, 400_000),
        ("fake:a", None, None, 16, 400_000),
    ],
)
def test_defaults_match_what_each_setting_gave_on_its_own(key, num_ctx, think, rounds, budget):
    assert _profiles().for_key(key) == ModelProfile(
        key=key,
        num_ctx=num_ctx,
        think=think,
        max_rounds=rounds,
        max_prompt_tokens=budget,
        rpd=KEY_RPD_OVERRIDES.get(key),
    )


def test_every_env_var_still_works():
    profile = _profiles(
        {
            "NUM_CTX_BY_MODEL": "lan:big*=65536",
            "THINK_BY_MODEL": "lan:big*=low",
            "CYCLE_MAX_ROUNDS": "9",
            "CYCLE_MAX_ROUNDS_BY_MODEL": "lan:big*=40",
            "CYCLE_MAX_PROMPT_TOKENS": "1000",
            "CYCLE_MAX_PROMPT_TOKENS_BY_MODEL": "lan:big*=5000",
        }
    )
    big = profile.for_key("lan:big:1b")
    assert (big.num_ctx, big.think, big.max_rounds, big.max_prompt_tokens) == (65536, "low", 40, 5000)
    other = profile.for_key("lan:other:1b")
    assert (other.num_ctx, other.think, other.max_rounds, other.max_prompt_tokens) == (
        LAN_DEFAULT_NUM_CTX,
        None,
        9,
        1000,
    )


def test_opt_outs_still_work():
    profile = _profiles(
        {
            "NUM_CTX_BY_MODEL": "off",
            "THINK_BY_MODEL": "none",
            "CYCLE_MAX_ROUNDS_BY_MODEL": "off",
            "CYCLE_MAX_PROMPT_TOKENS_BY_MODEL": "off",
        }
    ).for_key("lan:qwen3.8:27b-mlx")
    assert profile.num_ctx == LAN_DEFAULT_NUM_CTX
    assert profile.think is None
    assert profile.max_rounds == 16
    assert profile.max_prompt_tokens == 400_000


def test_first_pattern_wins():
    profiles = _profiles({"CYCLE_MAX_ROUNDS_BY_MODEL": "gemini:*=20,gemini:special=40"})
    assert profiles.for_key("gemini:special").max_rounds == 20


def test_rounds_never_above_the_ceiling():
    profiles = ModelProfiles(max_rounds=1000, max_rounds_by_model=[("a:*", 999)])
    assert profiles.for_key("a:x").max_rounds == CYCLE_MAX_ROUNDS_HARD_CEILING
    assert profiles.for_key("b:x").max_rounds == CYCLE_MAX_ROUNDS_HARD_CEILING


def test_zero_budget_is_kept_as_no_budget():
    profiles = ModelProfiles(max_prompt_tokens_by_model=[("lan:*", 0)])
    assert profiles.for_key("lan:x").max_prompt_tokens == 0


def test_chain_and_ledger_read_from_the_profile(tmp_path):
    settings = load_settings(
        {
            "LLM_CHAIN": "lan:qwen3.8:27b-mlx,ollama:qwen3:4b,gemini:gemini-3.8-flash",
            "GEMINI_API_KEY": "k",
        }
    )
    chain = ProviderChain.from_settings(settings, Ledger({}, tmp_path / "ledger.json"))
    assert [p.context_tokens for p in chain.providers] == [131072, 16384, None]
    assert chain.providers[0]._think == "xhigh"
    assert limits_from_settings(settings)["gemini:gemini-3.8-flash"].rpd == 11


# -- the cap function ------------------------------------------------------


def _after(state, rounds, new_cap, over_budget=False, nudge_sent=False, runway=None):
    return after_reply(
        state,
        rounds=rounds,
        new_cap=new_cap,
        over_budget=over_budget,
        nudge_sent=nudge_sent,
        runway=runway,
    )


def test_plain_reply_takes_the_models_cap():
    assert _after(CapState(16), 1, 32) == CapState(32)


def test_cap_drop_outside_the_wrap_up_window_just_applies():
    assert _after(CapState(32), 5, 16) == CapState(16)


def test_cap_drop_into_the_window_is_rescued_once():
    state = _after(CapState(32), 15, 16)
    assert state.floor_cap == 15 + WRAP_UP_ROUNDS_BEFORE_CAP
    assert state.effective_cap == 17


def test_rescue_floor_is_frozen():
    state = _after(CapState(32), 15, 16)
    again = _after(state, 16, 10, nudge_sent=True)
    assert again.floor_cap == state.floor_cap
    assert again.effective_cap == 17


def test_rescue_does_not_fire_once_the_nudge_has_been_sent():
    state = _after(CapState(32), 15, 16, nudge_sent=True)
    assert state.floor_cap is None
    assert state.effective_cap == 16


def test_higher_cap_above_the_floor_wins():
    state = _after(CapState(32), 15, 16)
    assert _after(state, 16, 32, nudge_sent=True).effective_cap == 32


def test_budget_stop_is_exact_and_sticky():
    state = _after(CapState(32), 4, 32, over_budget=True)
    assert state.budget_stopped is True
    assert state.floor_cap == 6
    assert state.effective_cap == 6
    # A later round on a generous model cannot undo it.
    later = _after(state, 5, 64)
    assert later.effective_cap == 6
    assert later.budget_stopped is True


def test_floor_is_clamped_to_the_hard_ceiling():
    state = _after(CapState(CYCLE_MAX_ROUNDS_HARD_CEILING), CYCLE_MAX_ROUNDS_HARD_CEILING - 1, 1)
    assert state.floor_cap == CYCLE_MAX_ROUNDS_HARD_CEILING


def test_quota_clamps_last_over_the_rescue_floor_too():
    rescued = _after(CapState(32), 15, 16, runway=1)
    assert rescued.floor_cap == 17
    assert rescued.effective_cap == 16
    assert rescued.quota_stopped is True


def test_quota_with_room_to_spare_changes_nothing():
    state = _after(CapState(16), 3, 16, runway=100)
    assert state.effective_cap == 16
    assert state.quota_stopped is False


def test_quota_stop_is_re_derived_each_round():
    clamped = _after(CapState(32), 5, 32, runway=1)
    assert clamped.effective_cap == 6
    assert _after(clamped, 6, 32, runway=None) == CapState(32, floor_cap=None)


def test_function_does_not_mutate_its_input():
    state = CapState(32)
    _after(state, 15, 16, over_budget=True)
    assert state == CapState(32)


@pytest.mark.parametrize(
    ("cap", "rounds", "sent", "expected"),
    [
        (16, 13, False, False),
        (16, 14, False, True),
        (16, 15, False, True),
        (16, 14, True, False),
    ],
)
def test_nudge_due(cap, rounds, sent, expected):
    assert nudge_due(CapState(cap), rounds=rounds, nudge_sent=sent) is expected


# -- the report ------------------------------------------------------------


def _reply(key="m:a", calls=()) -> dict:
    return {
        "type": "reply",
        "chain_key": key,
        "tool_calls": [{"id": c[0], "name": c[1], "args": c[2]} for c in calls],
    }


def _result(cid, name, result) -> dict:
    return {"type": "tool_result", "id": cid, "name": name, "result": result}


def test_summarize_a_nudged_cycle_that_ended_cleanly():
    events = [
        {"type": "opening", "angle": "Review one expert. Use expert_overview..."},
        _reply(calls=[("a", "review_expert", {})]),
        _result("a", "review_expert", json.dumps({"ok": True})),
        {"type": "message", "kind": "nudge", "content": "wrap up"},
        _reply(calls=[("e", "end_cycle", {"summary": "s"})]),
        _result("e", "end_cycle", json.dumps({"ok": True})),
        {"type": "cycle_end", "status": "ok", "cap": 8},
    ]

    cycle = summarize("brain", events)

    assert cycle.rounds == 2
    assert cycle.last_model == "m:a"
    assert cycle.nudged is True
    assert cycle.ended_after_nudge is True
    assert cycle.status == "ok"
    assert "review_expert" in cycle.tools


def test_nudge_with_no_end_cycle_after_is_not_counted_as_ending():
    events = [
        _reply(calls=[("e", "end_cycle", {})]),
        _result("e", "end_cycle", json.dumps({"error": "still owed"})),
        {"type": "message", "kind": "nudge"},
        _reply(calls=[("a", "list_facts", {})]),
        _result("a", "list_facts", "[]"),
        {"type": "cycle_end", "status": "max_rounds", "cap": 2},
    ]
    assert summarize("brain", events).ended_after_nudge is False


def test_end_cycle_before_the_nudge_does_not_count_as_after():
    events = [
        _reply(calls=[("e", "end_cycle", {})]),
        _result("e", "end_cycle", json.dumps({"ok": True})),
    ]
    cycle = summarize("brain", events)
    assert cycle.ended is True
    assert cycle.ended_after_nudge is False


def test_junk_refusals_go_to_the_model_that_wrote_them():
    events = [
        _reply("m:weak", [("a", "write_fact", {"name": "x"})]),
        _result("a", "write_fact", json.dumps({"error": "refused: body is empty"})),
        _reply("m:strong", [("b", "write_fact", {"name": "y"})]),
        _result("b", "write_fact", json.dumps({"error": "something else"})),
    ]
    cycle = summarize("brain", events)
    assert cycle.junk_refusals == {"m:weak": 1}
    assert cycle.rounds_by_model == {"m:weak": 1, "m:strong": 1}


def test_synthetic_end_cycle_does_not_count_as_ending():
    events = [
        _reply(calls=[("e", "end_cycle", {})]),
        {
            "type": "tool_result",
            "id": "e",
            "name": "end_cycle",
            "synthetic": "not_run",
            "result": json.dumps({"ok": True}),
        },
    ]
    assert summarize("brain", events).ended is False


def test_missing_cycle_end_reads_as_unfinished():
    assert summarize("brain", [_reply()]).status == "unfinished"


def test_build_report_per_model_numbers():
    cycles = [
        summarize(
            "brain",
            [
                {"type": "opening", "angle": "Review one expert. x"},
                _reply("m:a", [("r", "review_expert", {})]),
                _reply("m:b"),
                {"type": "cycle_end", "status": "ok", "cap": 2},
            ],
        ),
        summarize(
            "energy",
            [
                {"type": "opening", "angle": "Review one expert. x"},
                _reply("m:b"),
                {"type": "context_fit", "chain_key": "m:b", "trimmed": [3]},
                {"type": "resume"},
                {"type": "cycle_end", "status": "max_rounds", "cap": 9},
            ],
        ),
    ]

    report = build_report(cycles)

    assert report["cycles"] == 2
    a = report["models"]["m:a"]
    b = report["models"]["m:b"]
    # m:a answered one round but finished no cycle.
    assert (a["cycles"], a["rounds_answered"]) == (0, 1)
    assert (b["cycles"], b["rounds_answered"]) == (2, 2)
    assert b["at_cap"] == 1
    assert b["status"] == {"ok": 1, "max_rounds": 1}
    assert b["context_trims"] == 1
    assert b["resumes"] == 1
    assert report["angles"]["Review one expert"] == {"offered": 2, "taken": 1}


def test_trims_go_to_the_model_they_were_for():
    """A trim for a small window, in a cycle a large one finished."""
    cycle = summarize(
        "brain",
        [
            {"type": "context_fit", "chain_key": "lan:small", "trimmed": [3]},
            {"type": "context_fit", "chain_key": "lan:small", "trimmed": []},
            _reply("anthropic:big"),
        ],
    )
    models = build_report([cycle])["models"]
    assert models["lan:small"]["context_trims"] == 1
    assert models["anthropic:big"]["context_trims"] == 0


def test_a_cycle_with_no_reply_is_not_an_angle_offered():
    """It had no chance to follow it, so it must not read as the angle ignored."""
    angle = "Review one expert. x"
    blocked = summarize("energy", [{"type": "opening", "angle": angle}])
    followed = summarize(
        "energy",
        [{"type": "opening", "angle": angle}, _reply(calls=[("a", "review_expert", {})])],
    )
    assert build_report([blocked, blocked, followed])["angles"]["Review one expert"] == {
        "offered": 1,
        "taken": 1,
    }


def test_angle_with_no_signal_reports_no_taken_count():
    cycle = summarize("energy", [{"type": "opening", "angle": EXPERT_ANGLES[0]}, _reply()])
    report = build_report([cycle])
    assert report["angles"]["Find the thing that looks wrong"] == {"offered": 1, "taken": None}
    assert "-" in render(report)


def test_write_fact_named_signal():
    angle = next(a for a in BRAIN_ANGLES if a.startswith("Judge yourself"))
    took = summarize(
        "brain",
        [
            {"type": "opening", "angle": angle},
            _reply(calls=[("a", "write_fact", {"name": "self_review"})]),
        ],
    )
    other = summarize(
        "brain",
        [
            {"type": "opening", "angle": angle},
            _reply(calls=[("a", "write_fact", {"name": "pool"})]),
        ],
    )
    assert build_report([took, other])["angles"]["Judge yourself"] == {"offered": 2, "taken": 1}


def test_every_angle_the_loop_hands_out_has_a_signal_entry():
    """A renamed angle would otherwise fall out of the proxy without a word."""
    for angle in (*BRAIN_ANGLES, *EXPERT_ANGLES):
        assert any(angle.startswith(prefix) for prefix in ANGLE_SIGNALS), angle
    used = {
        prefix
        for prefix in ANGLE_SIGNALS
        if any(a.startswith(prefix) for a in (*BRAIN_ANGLES, *EXPERT_ANGLES))
    }
    assert used == set(ANGLE_SIGNALS)


async def test_report_reads_real_logs_plain_and_gzipped(brain_dir, expert_dir, tmp_path, wall, capsys):
    root = brain_dir.root.parent
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [
            reply("", call("write_fact", "a", name="x", title="t", body="up")),
            reply("", end()),
        ],
    )
    await loop.run_cycle()
    expert, _ = build_loop(expert_dir, tmp_path, wall, [reply("", end())])
    await expert.run_cycle()

    # A cycle a crash left behind reads the same, just unfinished.
    directory = expert_dir.root / "sessions"
    (directory / "20270115-090000-cccc.jsonl").write_text(
        json.dumps({"seq": 1, "type": "reply", "chain_key": "fake:1", "tool_calls": []}) + "\n"
    )

    cycles = load_cycles(root)

    assert sorted(c.agent for c in cycles) == ["brain", "energy", "energy"]
    report = build_report(cycles)
    assert report["models"]["fake:1"]["junk_fact_refusals"] == 1
    assert report["models"]["fake:1"]["status"]["unfinished"] == 1

    assert main(["--memory", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cycles"] == 3
    assert main(["--memory", str(root)]) == 0
    text = capsys.readouterr().out
    assert "fake:1" in text
    assert "junk refused" in text
    # And it wrote nothing.
    assert all(p.exists() for p in session_files(directory))


def test_days_filter_uses_the_cycle_id(tmp_path):
    directory = tmp_path / "brain" / "sessions"
    directory.mkdir(parents=True)
    line = json.dumps({"seq": 1, "type": "reply", "chain_key": "m", "tool_calls": []}) + "\n"
    (directory / "20270110-120000-aaaa.jsonl.gz").write_bytes(gzip.compress(line.encode()))
    (directory / "20261201-120000-bbbb.jsonl.gz").write_bytes(gzip.compress(line.encode()))

    assert len(load_cycles(tmp_path, days=7, now=1_800_000_000.0)) == 1
    assert len(load_cycles(tmp_path)) == 2


def test_empty_memory_is_not_an_error(tmp_path, capsys):
    assert main(["--memory", str(tmp_path)]) == 0
    assert "0 cycle(s)" in capsys.readouterr().out


async def test_caps_on_the_log_match_what_the_cycle_ran_with(brain_dir, tmp_path, wall):
    loop, _ = build_loop(
        brain_dir,
        tmp_path,
        wall,
        [reply("", call("list_facts", "a")), reply("", end())],
        max_rounds=5,
    )
    await loop.run_cycle()
    events = read_events(next(iter(session_files(brain_dir.root / "sessions"))))
    caps = [e["caps"] for e in events if e["type"] == "reply"]
    assert caps[0] == {
        "effective": 5,
        "budget_stopped": False,
        "floor": None,
        "budget_hit": 400_000,
        "quota_stopped": False,
    }
