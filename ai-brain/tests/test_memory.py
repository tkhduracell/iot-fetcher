from datetime import timedelta

import pytest

from ai_brain.memory import MemoryDir, safe_name, split_frontmatter


def test_safe_name_rejects_paths():
    for bad in ["../x", "a/b", "A", "", "x" * 70, ".hidden"]:
        with pytest.raises(ValueError):
            safe_name(bad)
    assert safe_name("pool-temp_2026.v1") == "pool-temp_2026.v1"


def test_fact_roundtrip_and_listing(brain_dir):
    brain_dir.write_fact("pool", "Pool temperature", "Pool is 28C")
    fact = brain_dir.read_fact("pool")
    assert fact.title == "Pool temperature"
    assert fact.body == "Pool is 28C"
    assert brain_dir.read_fact("missing") is None
    assert brain_dir.list_facts() == ["pool"]
    assert not list(brain_dir.root.glob("facts/*.tmp"))


def test_journal_append_uses_day_file(brain_dir, clock):
    brain_dir.append_journal("woke up")
    assert (brain_dir.root / "journal" / "2026-09-06.md").read_text() == "10:00  woke up\n"
    assert "woke up" in brain_dir.journal_text(days=2)


def test_goals_identity_brain_only(brain_dir, expert_dir):
    brain_dir.rewrite_goals("# Goals\n- learn")
    assert brain_dir.goals_text() == "# Goals\n- learn"
    with pytest.raises(ValueError):
        expert_dir.rewrite_goals("x")
    with pytest.raises(ValueError):
        expert_dir.rewrite_identity("x")


def test_notes_lifecycle(brain_dir):
    p = brain_dir.drop_note("energy", "spa heater ran 6h")
    notes = brain_dir.unread_notes()
    assert len(notes) == 1 and notes[0].sender == "energy" and "spa heater" in notes[0].body
    brain_dir.mark_done(notes)
    assert brain_dir.unread_notes() == []
    assert (brain_dir.root / "inbox" / "done" / p.name).exists()


def test_read_context_contains_sections(brain_dir):
    brain_dir.rewrite_identity("I am curious")
    brain_dir.rewrite_goals("- watch the pool")
    brain_dir.write_fact("pool", "Pool temperature", "28C")
    brain_dir.drop_note("filip", "hello")
    ctx = brain_dir.read_context("Be kind.")
    for s in ["Be kind.", "I am curious", "watch the pool", "pool", "from: filip", "hello"]:
        assert s in ctx
    assert "28C" not in ctx  # fact bodies load on demand


def test_needs_compaction(brain_dir):
    assert not brain_dir.needs_compaction()
    for i in range(41):
        brain_dir.write_fact(f"f{i}", "x", "x")
    assert brain_dir.needs_compaction()


def test_seed_from_copies_once(tmp_path, expert_dir):
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    (seed / "personas" / "energy.md").write_text("Energy expert")
    expert_dir.seed_from(seed)
    assert expert_dir.persona_text() == "Energy expert"
    (seed / "personas" / "energy.md").write_text("changed")
    expert_dir.seed_from(seed)
    assert expert_dir.persona_text() == "Energy expert"


# --- persona frontmatter ---------------------------------------------------


def test_split_frontmatter_separates_meta_from_body():
    meta, body = split_frontmatter("---\nemoji: ⚡\ncolor: blue\n---\nI am the energy expert.\n")
    assert meta == {"emoji": "⚡", "color": "blue"}
    assert body == "I am the energy expert.\n"


def test_split_frontmatter_with_no_block_returns_the_whole_text_as_body():
    meta, body = split_frontmatter("Just a persona, no frontmatter.")
    assert meta == {}
    assert body == "Just a persona, no frontmatter."


def test_split_frontmatter_ignores_an_unparsable_line():
    meta, _ = split_frontmatter("---\nemoji: ⚡\nnot a key value line\n---\nbody")
    assert meta == {"emoji": "⚡"}


def test_persona_text_strips_frontmatter_but_persona_meta_keeps_it(tmp_path, expert_dir):
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    (seed / "personas" / "energy.md").write_text("---\nemoji: ⚡\n---\nI am the energy expert.\n")
    expert_dir.seed_from(seed)

    assert expert_dir.persona_text() == "I am the energy expert."
    assert expert_dir.persona_meta() == {"emoji": "⚡"}


def test_seed_from_backfills_frontmatter_onto_an_already_live_persona(tmp_path, expert_dir):
    """A persona seeded before this field existed gets the seed's current
    frontmatter added in front -- but never touches the body underneath,
    which may since have been hand-edited or (for the brain) rewritten."""
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    # Live persona already exists, with no frontmatter -- as if seeded before
    # this field was invented.
    expert_dir.persona_path.parent.mkdir(parents=True, exist_ok=True)
    expert_dir.persona_path.write_text("I am the energy expert, already live.")

    (seed / "personas" / "energy.md").write_text(
        "---\nemoji: ⚡\n---\nI am the energy expert, from the seed.\n"
    )
    expert_dir.seed_from(seed)

    assert expert_dir.persona_meta() == {"emoji": "⚡"}
    # The live body is untouched -- only the seed's body would have said
    # "from the seed".
    assert expert_dir.persona_text() == "I am the energy expert, already live."


def test_seed_from_backfill_is_idempotent(tmp_path, expert_dir):
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    (seed / "personas" / "energy.md").write_text("---\nemoji: ⚡\n---\nseed body\n")
    expert_dir.seed_from(seed)
    first = expert_dir.persona_path.read_text(encoding="utf-8")

    expert_dir.seed_from(seed)  # a second boot, same seed
    assert expert_dir.persona_path.read_text(encoding="utf-8") == first


def test_seed_from_does_nothing_when_the_seed_has_no_frontmatter(tmp_path, expert_dir):
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    (seed / "personas" / "energy.md").write_text("plain seed, no frontmatter")
    expert_dir.seed_from(seed)
    assert expert_dir.persona_meta() == {}

    (seed / "personas" / "energy.md").write_text("still plain")
    expert_dir.seed_from(seed)
    assert expert_dir.persona_text() == "plain seed, no frontmatter"


def test_drop_note_counter_avoids_collisions(brain_dir):
    names = [brain_dir.drop_note("energy", f"n{i}").name for i in range(3)]
    assert names == [
        "20260906T100000-energy-0.md",
        "20260906T100000-energy-1.md",
        "20260906T100000-energy-2.md",
    ]
    assert [n.body for n in brain_dir.unread_notes()] == ["n0", "n1", "n2"]


def test_purge_done_only_removes_old_notes(brain_dir, clock):
    from datetime import timedelta

    brain_dir.drop_note("energy", "old")
    brain_dir.mark_done(brain_dir.unread_notes())
    clock.state["now"] += timedelta(days=40)
    brain_dir.drop_note("energy", "recent")
    brain_dir.mark_done(brain_dir.unread_notes())
    assert brain_dir.purge_done(30) == 1
    assert [p.name for p in (brain_dir.root / "inbox" / "done").glob("*.md")] == [
        "20261016T100000-energy-0.md"
    ]


def test_expert_ensure_has_no_outbox(expert_dir):
    assert (expert_dir.root / "journal").is_dir()
    assert (expert_dir.root / "facts").is_dir()
    assert (expert_dir.root / "inbox" / "done").is_dir()
    assert not (expert_dir.root / "outbox").exists()


def test_brain_seed_creates_identity_and_goals(brain_dir, tmp_path):
    seed = tmp_path / "seed"
    (seed / "personas").mkdir(parents=True)
    (seed / "personas" / "brain.md").write_text("blank slate")
    brain_dir.seed_from(seed)
    assert brain_dir.persona_text() == "blank slate"
    assert (brain_dir.root / "goals.md").exists()
    brain_dir.rewrite_goals("- a goal")
    brain_dir.seed_from(seed)
    assert brain_dir.goals_text() == "- a goal"


# --- pruning: the compaction trigger must be clearable ---------------------


def test_delete_fact_roundtrip(brain_dir):
    brain_dir.write_fact("pool", "Pool temperature", "Pool is 28C")
    assert brain_dir.delete_fact("pool") is True
    assert brain_dir.read_fact("pool") is None
    assert brain_dir.list_facts() == []


def test_delete_fact_reports_a_missing_fact(brain_dir):
    assert brain_dir.delete_fact("never-written") is False


def test_delete_fact_validates_the_name(brain_dir):
    with pytest.raises(ValueError):
        brain_dir.delete_fact("../etc/passwd")


def test_delete_fact_clears_the_compaction_trigger(brain_dir):
    """Overwriting a fact still counts, so without delete the latch never opens."""
    for i in range(41):
        brain_dir.write_fact(f"f{i}", "x", "x")
    assert brain_dir.needs_compaction()

    brain_dir.delete_fact("f0")
    assert len(brain_dir.list_facts()) == 40
    assert not brain_dir.needs_compaction()


def test_prune_journal_removes_old_files_and_keeps_recent_ones(brain_dir, clock):
    now = clock.state["now"]
    old = (now - timedelta(days=31)).strftime("%Y-%m-%d")
    recent = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    for day in (old, recent):
        (brain_dir.journal_dir / f"{day}.md").write_text("07:00  a line\n", encoding="utf-8")
    (brain_dir.journal_dir / "notes.md").write_text("not a date\n", encoding="utf-8")

    assert brain_dir.prune_journal() == 1
    assert sorted(p.name for p in brain_dir.journal_dir.glob("*.md")) == [
        f"{recent}.md",
        "notes.md",
    ]


def test_prune_journal_clears_the_compaction_trigger(brain_dir, clock):
    """The journal grows a file a day forever; on day 31 the hint must not latch."""
    now = clock.state["now"]
    for i in range(40):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        (brain_dir.journal_dir / f"{day}.md").write_text("x\n", encoding="utf-8")
    assert brain_dir.needs_compaction()

    brain_dir.prune_journal()
    assert len(list(brain_dir.journal_dir.glob("*.md"))) <= 30
    assert not brain_dir.needs_compaction()


def test_prune_journal_on_a_missing_directory(tmp_path, clock):
    fresh = MemoryDir(tmp_path / "nothing", "brain", is_brain=True, clock=clock)
    assert fresh.prune_journal() == 0


# --- journal_days ---------------------------------------------------------


def test_journal_days_lists_the_dates_that_have_files(brain_dir):
    for day in ("2026-09-04", "2026-09-06", "2026-09-05"):
        (brain_dir.journal_dir / f"{day}.md").write_text("x\n", encoding="utf-8")
    (brain_dir.journal_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    assert brain_dir.journal_days() == ["2026-09-04", "2026-09-05", "2026-09-06"]


def test_journal_days_is_empty_without_a_journal_dir(tmp_path, clock):
    fresh = MemoryDir(tmp_path / "nothing", "brain", is_brain=True, clock=clock)
    assert fresh.journal_days() == []


# --- fact freshness and write counts --------------------------------------


def test_fact_stats_counts_writes_and_keeps_the_first_time(brain_dir, clock):
    brain_dir.write_fact("pool", "Pool temperature", "28C")
    first = clock.state["now"].timestamp()
    clock.state["now"] += timedelta(hours=3)
    brain_dir.write_fact("pool", "Pool temperature", "29C")
    brain_dir.write_fact("spa", "Spa temperature", "38C")

    stats = brain_dir.fact_stats()
    assert [s.name for s in stats] == ["pool", "spa"]
    pool = stats[0]
    assert pool.title == "Pool temperature"
    assert pool.writes == 2
    assert pool.first_written_at == first
    assert pool.written_at >= pool.first_written_at


def test_fact_stats_sidecar_never_leaks_into_the_fact_list(brain_dir):
    brain_dir.write_fact("pool", "Pool temperature", "28C")
    assert (brain_dir.facts_dir / "_meta.json").exists()
    assert brain_dir.list_facts() == ["pool"]
    assert [s.name for s in brain_dir.fact_stats()] == ["pool"]


def test_delete_fact_drops_the_sidecar_entry(brain_dir):
    brain_dir.write_fact("pool", "Pool temperature", "28C")
    brain_dir.write_fact("pool", "Pool temperature", "29C")
    assert brain_dir.delete_fact("pool") is True

    brain_dir.write_fact("pool", "Pool temperature", "30C")
    assert brain_dir.fact_stats()[0].writes == 1


def test_fact_stats_degrades_when_the_sidecar_is_corrupt(brain_dir):
    """The facts are the truth; a broken sidecar must not hide them."""
    brain_dir.write_fact("pool", "Pool temperature", "28C")
    (brain_dir.facts_dir / "_meta.json").write_text("{not json", encoding="utf-8")

    stats = brain_dir.fact_stats()
    assert len(stats) == 1
    assert stats[0].writes == 1
    assert stats[0].first_written_at == stats[0].written_at
    # The sidecar (and its "title": "Pool temperature") is gone -- the title
    # falls back to the body itself, same as any pre-title fact would.
    assert stats[0].title == "28C"


def test_fact_stats_without_a_facts_dir(tmp_path, clock):
    fresh = MemoryDir(tmp_path / "nothing", "brain", is_brain=True, clock=clock)
    assert fresh.fact_stats() == []


# --- identity / goals revision history ------------------------------------


def test_goals_history_keeps_the_previous_body(brain_dir, clock):
    brain_dir.rewrite_goals("- a")
    clock.state["now"] += timedelta(minutes=1)
    brain_dir.rewrite_goals("- b")
    clock.state["now"] += timedelta(minutes=1)
    brain_dir.rewrite_goals("- c")

    history = brain_dir.goals_history()
    assert [r.body for r in history] == ["- b", "- a"]
    assert history[0].at > history[1].at
    assert brain_dir.goals_text() == "- c"


def test_identity_history_ignores_an_unchanged_rewrite(brain_dir):
    brain_dir.rewrite_identity("I am curious")
    brain_dir.rewrite_identity("I am curious")
    assert brain_dir.identity_history() == []

    brain_dir.rewrite_identity("I am patient")
    assert [r.body for r in brain_dir.identity_history()] == ["I am curious"]


def test_history_is_capped_and_limited(brain_dir):
    for i in range(25):
        brain_dir.rewrite_goals(f"- {i}")

    assert len(list((brain_dir.history_dir / "goals").glob("*.md"))) == 20
    assert len(brain_dir.goals_history()) == 10
    assert len(brain_dir.goals_history(limit=50)) == 20
    # Oldest revisions are the ones pruned.
    assert brain_dir.goals_history(limit=50)[-1].body == "- 4"


def test_history_is_empty_without_a_history_dir(brain_dir):
    assert brain_dir.identity_history() == []
    assert brain_dir.goals_history() == []


def test_history_skips_a_file_that_is_not_a_revision(brain_dir):
    brain_dir.rewrite_goals("- a")
    brain_dir.rewrite_goals("- b")
    (brain_dir.history_dir / "goals" / "notes.md").write_text("junk", encoding="utf-8")

    assert [r.body for r in brain_dir.goals_history()] == ["- a"]


# --- gaps: known unknowns --------------------------------------------------


def test_open_gap_is_idempotent_on_the_slug(brain_dir, clock):
    first = brain_dir.open_gap("Why does the pool cool at night?", "blocks heating advice")
    assert first.id == "why-does-the-pool-cool-at-night"
    assert first.closed_at is None and first.answer == ""

    clock.state["now"] += timedelta(hours=2)
    again = brain_dir.open_gap("Why does the pool cool at night?", "something else")
    assert again == first
    assert len(brain_dir.gaps()) == 1


def test_close_gap_and_reopen(brain_dir, clock):
    gap = brain_dir.open_gap("Is the spa heater on?", "blocks the energy note")
    assert brain_dir.close_gap(gap.id, "yes, since 06:00") is True
    assert brain_dir.gaps() == []

    closed = brain_dir.gaps(include_closed=True)[0]
    assert closed.answer == "yes, since 06:00" and closed.closed_at is not None

    reopened = brain_dir.open_gap("Is the spa heater on?")
    assert reopened.closed_at is None and reopened.answer == ""
    assert reopened.opened_at == gap.opened_at
    assert reopened.why == "blocks the energy note"


def test_close_gap_reports_an_unknown_gap(brain_dir):
    assert brain_dir.close_gap("never-opened", "x") is False


def test_close_gap_validates_the_name(brain_dir):
    with pytest.raises(ValueError):
        brain_dir.close_gap("../etc/passwd", "x")


def test_gaps_are_newest_opened_first(brain_dir, clock):
    brain_dir.open_gap("first question")
    clock.state["now"] += timedelta(hours=1)
    brain_dir.open_gap("second question")

    assert [g.question for g in brain_dir.gaps()] == ["second question", "first question"]


def test_gaps_skips_a_corrupt_file(brain_dir):
    brain_dir.open_gap("a real question")
    (brain_dir.gaps_dir / "broken.json").write_text("{not json", encoding="utf-8")

    assert [g.question for g in brain_dir.gaps()] == ["a real question"]


def test_gaps_without_a_gaps_dir(tmp_path, clock):
    fresh = MemoryDir(tmp_path / "nothing", "brain", is_brain=True, clock=clock)
    assert fresh.gaps() == []


def test_open_gap_slug_survives_punctuation_and_length(brain_dir):
    gap = brain_dir.open_gap("Vad hände med poolpumpen?!")
    assert gap.id == "vad-h-nde-med-poolpumpen"
    long_gap = brain_dir.open_gap("x " * 100)
    assert safe_name(long_gap.id) == long_gap.id


# --- gaps reach the model --------------------------------------------------


def test_read_context_lists_open_gaps(brain_dir):
    brain_dir.open_gap("Why is the spa cold?", "blocks the heating advice")
    brain_dir.open_gap("Who left the door open?")
    closed = brain_dir.open_gap("Already answered?")
    brain_dir.close_gap(closed.id, "yes")

    ctx = brain_dir.read_context("Be kind.")
    assert "## Öppna luckor" in ctx
    assert "Why is the spa cold? (blocks the heating advice)" in ctx
    assert "Who left the door open?" in ctx
    assert "Already answered?" not in ctx


def test_read_context_omits_the_gap_section_when_there_are_none(brain_dir):
    assert "Öppna luckor" not in brain_dir.read_context("Be kind.")


def test_read_context_caps_the_gap_section(brain_dir, clock):
    for i in range(15):
        clock.state["now"] += timedelta(minutes=1)
        brain_dir.open_gap(f"question number {i}")

    ctx = brain_dir.read_context("Be kind.")
    section = ctx.split("## Öppna luckor\n")[1]
    assert len(section.strip().splitlines()) == 10
    assert "question number 14" in section
    assert "question number 4" not in section
