import pytest

from ai_brain.memory import safe_name


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
