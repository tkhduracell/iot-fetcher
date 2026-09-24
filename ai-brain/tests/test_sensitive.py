import json

import pytest

from ai_brain.sensitive import is_sensitive

DENIED_NAMES = [
    ".env",
    ".env.local",
    ".env.production",
    "prod.env",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "server.pem",
    "client.key",
    "bundle.p12",
    "keystore.pfx",
    "app.jks",
    "password.txt",
    "secrets.yaml",
    "credentials.json",
    "api_token.txt",
    ".mcp.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".htpasswd",
]


@pytest.mark.parametrize("name", DENIED_NAMES)
def test_denied_filenames(name):
    assert is_sensitive(name) is True


ALLOWED_TEMPLATES = [
    ".env.example",
    ".env.template",
    ".env.sample",
    "wud/.env.example",
    "grafana/.env.example",
]


@pytest.mark.parametrize("name", ALLOWED_TEMPLATES)
def test_env_templates_stay_visible(name):
    assert is_sensitive(name) is False


ALLOWED_SOURCE_FILES = [
    "ai_brain/redact.py",
    "ai_brain/tools/introspect.py",
    "grafana/set-github-secrets.sh",
    "docs/token-design.md",
    "scripts/rotate_secret.sh",
    "src/secret_store.ts",
    "internal/secrets.go",
]


@pytest.mark.parametrize("name", ALLOWED_SOURCE_FILES)
def test_source_files_mentioning_keyish_words_are_not_denied(name):
    """secrets.py, set-github-secrets.sh and friends are ordinary source
    files whose job is handling secrets -- not a file that contains one."""
    assert is_sensitive(name) is False


def test_dot_env_variants_are_denied():
    assert is_sensitive(".env") is True
    assert is_sensitive(".env.local") is True
    assert is_sensitive("config.env") is True


def test_path_under_volumes_is_denied_regardless_of_filename():
    assert is_sensitive("volumes/ai-brain-memory/brain/identity.md") is True
    assert is_sensitive("volumes/foo/bar/README.md") is True


def test_path_under_dot_git_is_denied():
    assert is_sensitive(".git/config") is True
    assert is_sensitive("some/nested/.git/HEAD") is True


def test_an_ordinary_file_is_not_denied():
    assert is_sensitive("README.md") is False
    assert is_sensitive("pool-pump-planner/vm.go") is False
    assert is_sensitive("ai-brain/src/ai_brain/loop.py") is False


def test_service_account_json_is_denied_by_content():
    content = json.dumps(
        {"type": "service_account", "project_id": "x", "private_key": "-----BEGIN..."}
    ).encode()
    assert is_sensitive("gcp-key.json", content) is True
    # Name alone does not say so -- content is what catches it.
    assert is_sensitive("gcp-key.json") is False


def test_json_with_private_key_field_is_denied_even_without_the_type_marker():
    content = json.dumps({"private_key": "-----BEGIN PRIVATE KEY-----"}).encode()
    assert is_sensitive("weird-name.json", content) is True


def test_an_ordinary_json_file_is_not_denied():
    content = json.dumps({"name": "iot-fetcher", "version": "1.0.0"}).encode()
    assert is_sensitive("package.json", content) is False


def test_malformed_json_content_does_not_crash():
    assert is_sensitive("broken.json", b"{not: valid json") is False


def test_non_json_file_content_is_never_sniffed():
    # The .json-only sniff means a random file just happening to contain the
    # marker string is not swept up by name-only extensions this predicate
    # was never meant to open.
    assert is_sensitive("notes.txt", b'"type": "service_account"') is False
