"""Secret-shape masking, ai_brain.redact.

Every fixture below uses an obviously fake value (AIzaFAKE..., xoxb-fake-...,
etc) -- this repo is public, so no real key/token shape is reproduced, only
the structural pattern each real one follows.
"""

from __future__ import annotations

from ai_brain.redact import MASK, redact


# --- Google API keys -------------------------------------------------------


def test_redacts_google_api_key_in_a_url():
    text = (
        "fetching https://maps.googleapis.com/maps/api/geocode/json"
        "?address=Main+St&key=AIzaFAKEb1c2d3e4f5g6h7i8j9k0l1m2n3o4p5q"
    )
    out = redact(text)
    assert "AIza" not in out
    assert MASK in out
    assert "maps.googleapis.com" in out  # surrounding text survives


def test_redacts_google_api_key_bare():
    key = "AIzaFAKEb1c2d3e4f5g6h7i8j9k0l1m2n3o4p5q"
    assert key not in redact(f"key seen: {key}")


def test_redacts_google_oauth_access_token():
    out = redact("Authorization header carried ya29.FAKEtoken1234567890abcdefXYZ")
    assert "ya29." not in out


def test_redacts_google_refresh_token():
    out = redact("stored 1//FAKE0refreshTokenValue1234567890")
    assert "1//FAKE0" not in out


# --- key=value / token=value style params ----------------------------------


def test_redacts_key_query_param():
    out = redact("GET /x?key=abcDEF123456ghijkl")
    assert "abcDEF123456ghijkl" not in out
    assert "key=" + MASK in out


def test_redacts_token_query_param():
    out = redact("token=sekrit_abc123XYZ789&other=fine")
    assert "sekrit_abc123XYZ789" not in out
    assert "other=fine" in out


def test_redacts_api_key_kv():
    out = redact("api_key=abcdefgh12345678")
    assert "abcdefgh12345678" not in out


def test_redacts_access_token_kv_colon_style():
    out = redact('"access_token": "abcdefghij1234567890"')
    assert "abcdefghij1234567890" not in out


def test_redacts_password_kv():
    out = redact("password=SuperSecretValue123")
    assert "SuperSecretValue123" not in out


# --- Authorization / Bearer --------------------------------------------------


def test_redacts_authorization_bearer_header():
    out = redact("Authorization: Bearer abc123.def456-ghi789_jkl012")
    assert "abc123.def456-ghi789_jkl012" not in out
    assert "Authorization:" in out


def test_redacts_bare_bearer_token():
    out = redact("sent header Bearer abcDEFghi123456JKL")
    assert "abcDEFghi123456JKL" not in out
    assert "Bearer" in out


# --- JWTs --------------------------------------------------------------------


def test_redacts_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    out = redact(f"got token {jwt} in response")
    assert jwt not in out
    assert MASK in out


# --- Slack tokens --------------------------------------------------------------


def test_redacts_slack_bot_token():
    out = redact("SLACK_BOT_TOKEN=xoxb-fake-1234567890-abcdefghijklmnop")
    assert "xoxb-fake" not in out


def test_redacts_slack_user_token():
    out = redact("xoxp-fake-1111111111-2222222222-abcdefghijklmnopqrstuvwx")
    assert "xoxp-fake" not in out


# --- Emails --------------------------------------------------------------------


def test_redacts_email_address():
    out = redact("nickName held the account email someuser@example.com again")
    assert "someuser@example.com" not in out
    assert MASK in out


# --- key-ish phrase without = or : ------------------------------------------


def test_redacts_keyish_phrase_in_prose():
    out = redact("my api key is abcdefghij1234567890XYabcd==")
    assert "abcdefghij1234567890XYabcd==" not in out


# --- no-false-positive cases --------------------------------------------------


def test_leaves_timestamps_alone():
    line = "Container started at 2026-09-23T10:00:00Z"
    assert redact(line) == line


def test_leaves_uuids_alone():
    line = "request id: 550e8400-e29b-41d4-a716-446655440000"
    assert redact(line) == line


def test_leaves_container_ids_alone():
    line = "container id=deadbeef1234 state=running"
    assert redact(line) == line


def test_leaves_ordinary_sentence_with_word_key_alone():
    line = "cache key hit for /foo, 3 misses"
    assert redact(line) == line


def test_leaves_ip_addresses_alone():
    line = "connected to 10.0.0.5:8123"
    assert redact(line) == line


def test_leaves_plain_log_line_alone():
    line = "[aquatemp] poll ok, temp=27.4C, power=1200W"
    assert redact(line) == line


def test_leaves_short_numeric_value_after_key_word_alone():
    # Not secret-shaped: too short to be a real token, must not be eaten.
    line = "retry key=3"
    assert redact(line) == line


def test_empty_string_is_a_noop():
    assert redact("") == ""


def test_idempotent():
    text = "key=AIzaFAKEb1c2d3e4f5g6h7i8j9k0l1m2n3o4p5q and more"
    once = redact(text)
    assert redact(once) == once
