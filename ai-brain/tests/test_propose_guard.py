"""Unit tests for propose.py's junk-proposal guard.

These call junk_proposal_reason directly -- a pure function, no registry, no
approvals system -- because the point is the classification logic itself, not
the tool plumbing around it (that is covered in test_approvals.py, which
exercises the guard through the actual propose tool).
"""

import pytest

from ai_brain.tools.propose import junk_proposal_reason

# Real prompt text this whole feature exists to catch. Copied loosely from
# loop.py's BRAIN_ANGLES/CYCLE_INSTRUCTIONS rather than imported verbatim, so
# the test does not silently stop testing anything if the prompt wording
# changes -- it still has to overlap enough to trip the guard.
_ANGLE_ECHO = "Earn your keep. Find something that would genuinely help Filip and, if it touches the house, propose it."
_THREAD_ECHO = "Pick up an open thread. Read your recent journal, find something you left unexplained, and take it further."


def test_a_real_request_is_not_junk():
    assert junk_proposal_reason(
        "ha_todo_add", {"item": "replace the pool filter cartridge"}, "pool"
    ) is None


def test_a_real_sonos_say_is_not_junk():
    assert junk_proposal_reason(
        "sonos_say", {"text": "the garage door has been open for an hour"}, "garage"
    ) is None


def test_rejects_the_models_own_angle_as_the_item():
    # A neutral topic, deliberately, so this isolates the item-echo check
    # from the separate meta-topic check (see test_rejects_meta_topics).
    reason = junk_proposal_reason("ha_todo_add", {"item": _ANGLE_ECHO}, "helping-filip")
    assert reason is not None
    assert "restates your own instructions" in reason


def test_rejects_a_paraphrased_angle():
    # Same content, reworded -- the token-overlap check should still catch a
    # close paraphrase, not just an exact quote.
    paraphrase = (
        "Find something that would genuinely help Filip, and propose it if it "
        "touches the house"
    )
    reason = junk_proposal_reason("ha_todo_add", {"item": paraphrase}, "helping")
    assert reason is not None


def test_rejects_cycle_instructions_sentence_as_item():
    reason = junk_proposal_reason(
        "ha_todo_add",
        {"item": "Start the think cycle"},
        "think",
    )
    assert reason is not None


def test_rejects_unfinished_thought_style_item():
    reason = junk_proposal_reason("ha_todo_add", {"item": _THREAD_ECHO}, "unfinished-thought")
    assert reason is not None


@pytest.mark.parametrize(
    "topic",
    ["think", "think-cycle", "Think Cycle", "cycle", "wake_up", "next_wake", "greetings"],
)
def test_rejects_meta_topics(topic):
    reason = junk_proposal_reason(
        "ha_todo_add", {"item": "replace the pool filter cartridge"}, topic
    )
    assert reason is not None
    assert "thinking process" in reason


def test_does_not_reject_chat_topic_its_approvals_own_job():
    """'chat' is Filip's reserved topic, refused elsewhere with a more
    specific message; this guard must not shadow that with a generic one."""
    reason = junk_proposal_reason(
        "ha_todo_add", {"item": "replace the pool filter cartridge"}, "chat"
    )
    assert reason is None


def test_rejects_an_item_under_three_words():
    assert junk_proposal_reason("ha_todo_add", {"item": "check pool"}, "pool") is not None
    assert junk_proposal_reason("ha_todo_add", {"item": "pool"}, "pool") is not None


def test_rejects_an_item_that_is_just_the_topic_repeated():
    reason = junk_proposal_reason(
        "ha_todo_add", {"item": "roborock needs attention"}, "roborock needs attention"
    )
    assert reason is not None
    assert "topic repeated" in reason


def test_ha_service_kind_checked_on_its_own_text_field():
    # ha_service has no free-text field in KINDS ("service", "entity_id" are
    # both short identifiers, not prose an agent would restate a prompt
    # into) -- so nothing here should ever be flagged as an echoed angle.
    assert (
        junk_proposal_reason(
            "ha_service",
            {"service": "light.turn_off", "entity_id": "light.kitchen"},
            "lights",
        )
        is None
    )


def test_unknown_kind_is_not_junk_by_this_guard():
    # approvals.propose is what rejects an unknown kind; this guard only
    # judges the text of kinds it recognises.
    assert junk_proposal_reason("launch_missiles", {}, "oops") is None
