from datetime import timedelta

import pytest

from ai_brain.memory import MemoryDir, safe_name


def test_safe_name_rejects_paths():
    for bad in ["../x", "a/b", "A", "", "x" * 70, ".hidden"]:
        with pytest.raises(ValueError):
            safe_name(bad)
    assert safe_name("pool-temp_2026.v1") == "pool-temp_2026.v1"


def test_fact_roundtrip_and_listing(brain_dir):
    brain_dir.write_fact("pool", "Pool is 28C")
    assert brain_dir.read_fact("pool") == "Pool is 28C"
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
    brain_dir.write_fact("pool", "28C")
    brain_dir.drop_note("filip", "hello")
    ctx = brain_dir.read_context("Be kind.")
    for s in ["Be kind.", "I am curious", "watch the pool", "pool", "from: filip", "hello"]:
        assert s in ctx
    assert "28C" not in ctx  # fact bodies load on demand


def test_needs_compaction(brain_dir):
    assert not brain_dir.needs_compaction()
    for i in range(41):
        brain_dir.write_fact(f"f{i}", "x")
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
    brain_dir.write_fact("pool", "Pool is 28C")
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
        brain_dir.write_fact(f"f{i}", "x")
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
