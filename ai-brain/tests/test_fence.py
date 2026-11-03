import pytest

from ai_brain.fence import escape_fence, fence
from ai_brain.tools import wrap_external


def test_fence_wraps_text_with_its_source():
    assert fence("web", "hello") == '<external source="web">hello</external>'


def test_block_puts_the_tags_on_their_own_lines():
    assert fence("inbox", "topic: x\nbody", block=True) == (
        '<external source="inbox">\ntopic: x\nbody\n</external>'
    )


@pytest.mark.parametrize("closer", ["</external>", "</EXTERNAL>", "</External >", "<external source='x'>"])
def test_text_cannot_close_or_reopen_the_fence(closer):
    out = fence("inbox", f"before {closer} after", block=True)
    # Exactly the one opening tag and the one closing tag we wrote.
    assert out.lower().count("<external") == 1
    assert out.lower().count("</external") == 1
    assert out.startswith('<external source="inbox">')
    assert out.endswith("</external>")
    assert "before" in out and "after" in out


def test_escape_leaves_ordinary_text_alone():
    assert escape_fence("a < b, <b>bold</b>, externalities") == "a < b, <b>bold</b>, externalities"


@pytest.mark.parametrize("source", ["", "Web", 'a"b', "a b", "a>b"])
def test_a_bad_source_is_refused(source):
    with pytest.raises(ValueError):
        fence(source, "x")


def test_wrap_external_is_the_same_fence_with_masking():
    assert wrap_external("docker", "up 2 days") == fence("docker", "up 2 days")
    assert "AIza" not in wrap_external("docker", "key AIza" + "A" * 35)
    with pytest.raises(ValueError, match="fence: invalid source"):
        wrap_external("Bad Source", "x")
