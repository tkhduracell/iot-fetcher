"""Tools whose ``available`` predicate hides them when the config they need
is missing -- covers web_search (BRAVE_API_KEY), drive_search (gdrive URL)
and slack_post (both Slack tokens). web_fetch is deliberately not gated: it
works off any public URL, not a Brave key, so it stays offered regardless.
"""

from ai_brain.config import load_settings
from ai_brain.tools import ToolRegistry
from ai_brain.tools.drive import register_drive_tools
from ai_brain.tools.slack_tools import register_slack_tools
from ai_brain.tools.web import register_web_tools


def _names(registry: ToolRegistry, loop: str, settings) -> set[str]:
    return {s.name for s in registry.specs_for(loop, settings)}


def test_web_search_hidden_without_brave_key_web_fetch_stays():
    reg = ToolRegistry()
    register_web_tools(reg)

    without_key = _names(reg, "brain", load_settings({}))
    assert "web_search" not in without_key
    assert "web_fetch" in without_key

    with_key = _names(reg, "brain", load_settings({"BRAVE_API_KEY": "secret"}))
    assert "web_search" in with_key
    assert "web_fetch" in with_key


def test_drive_search_hidden_without_gdrive_url():
    reg = ToolRegistry()
    register_drive_tools(reg)

    without_url = _names(reg, "brain", load_settings({"GDRIVE_RAG_URL": ""}))
    assert "drive_search" not in without_url

    with_url = _names(reg, "brain", load_settings({}))  # config.py defaults it
    assert "drive_search" in with_url


def test_slack_post_hidden_without_both_slack_tokens():
    reg = ToolRegistry()
    register_slack_tools(reg)

    assert "slack_post" not in _names(reg, "brain", load_settings({}))
    assert "slack_post" not in _names(
        reg, "brain", load_settings({"SLACK_BOT_TOKEN": "xoxb-1"})
    )
    assert "slack_post" not in _names(
        reg, "brain", load_settings({"SLACK_APP_TOKEN": "xapp-1"})
    )

    configured = load_settings(
        {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_APP_TOKEN": "xapp-1"}
    )
    assert "slack_post" in _names(reg, "brain", configured)


def test_slack_post_still_refused_for_an_expert_regardless_of_config():
    """available narrows what a loop is offered; the loops allowlist still
    decides who may even ask -- the two checks are independent."""
    reg = ToolRegistry()
    register_slack_tools(reg)
    configured = load_settings(
        {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_APP_TOKEN": "xapp-1"}
    )
    assert "slack_post" not in _names(reg, "energy", configured)
