"""Tests for the reply-rendering module (candidate 4 deepening).

The whole Telegram formatting pipeline — flavor selection, escaping, splitting,
header assembly and the send-failure fallback — is exercised here through the
module's pure interface; nothing touches the network or the bot library.
"""

import importlib

fmt = importlib.import_module("lambda.formatting")


def test_markdown_flavor_escapes_only_the_legacy_subset():
    text = "a.b-c+d#e|f{g}h!i=j(k)l<m>n *bold* _em_ `code` [x] ~s~"
    rendered = fmt.format_text(text, "markdown")
    for char in ".-+#|{}!=()<>":
        assert f"\\{char}" in rendered
    # emphasis / code / link delimiters survive so intended markup renders
    for char in "*_`[]~":
        assert char in rendered


def test_markdown_flavor_leaves_adjacent_pipes_alone():
    # legacy parity: a pipe next to another pipe is not escaped (table/spoiler)
    assert fmt.format_text("a||b", "markdown") == "a||b"


def test_plain_flavor_escapes_the_whole_reserved_set():
    rendered = fmt.format_text(
        "3 * 4 = 12 a_b [c] (d) ~e~ `f` #g -h +i |j| {k} !l .m >n \\o",
        "plain",
    )
    for char in "*_[]()~`#+-=|{}!.>\\":
        assert f"\\{char}" in rendered


def test_plain_flavor_escapes_backslashes():
    assert fmt.format_text("a\\b", "plain") == "a\\\\b"


def test_markdownv2_flavor_passes_text_through():
    raw = "already \\*escaped\\* text"
    assert fmt.format_text(raw, "markdownv2") == raw


def test_resolve_format_defaults_to_markdown():
    assert fmt.resolve_format(None, None) == "markdown"
    assert fmt.resolve_format("gemini", None) == "markdown"


def test_resolve_format_prefers_declared_format():
    assert fmt.resolve_format("ollama", "plain") == "plain"


def test_resolve_format_falls_back_on_unknown_declared():
    assert fmt.resolve_format("gemini", "bogus") == "markdown"


def test_resolve_format_override_beats_declared():
    fmt.ENGINE_FORMAT_OVERRIDES["gemini"] = "plain"
    try:
        assert fmt.resolve_format("gemini", "markdown") == "plain"
    finally:
        del fmt.ENGINE_FORMAT_OVERRIDES["gemini"]


def test_split_utf16_keeps_each_part_within_the_budget():
    parts = fmt._split_utf16("a" * 9000, 4060)
    assert [len(part) for part in parts] == [4060, 4060, 880]
    assert "".join(parts) == "a" * 9000


def test_split_utf16_counts_astral_characters_as_two_units():
    emoji = "\U0001f600"
    parts = fmt._split_utf16(emoji * 3000, 4060)
    assert [len(part) for part in parts] == [2030, 970]
    assert "".join(parts) == emoji * 3000


def test_split_utf16_mixed_width_characters():
    text = "a" * 4059 + "\U0001f600" + "b" * 10
    parts = fmt._split_utf16(text, 4060)
    assert parts[0] == "a" * 4059  # the emoji needs 2 units and does not fit
    assert "".join(parts) == text


def test_split_utf16_empty_text_yields_no_parts():
    assert fmt._split_utf16("", 4060) == []


def test_assemble_engine_reply_numbers_and_renders_parts():
    parts = fmt.assemble_engine_reply("x" * 9000, "gemini")
    assert len(parts) == 3
    assert parts[0].startswith("*__gemini__*: 1 of 3\n")
    assert parts[1].startswith("*__gemini__*: 2 of 3\n")
    assert parts[2].startswith("*__gemini__*: 3 of 3\n")
    # every content character survives, headers only prefix the parts
    content = "".join(part.split("\n", 1)[1] for part in parts)
    assert content == "x" * 9000


def test_assemble_engine_reply_single_part_still_labeled():
    (part,) = fmt.assemble_engine_reply("hello", "EN-US")
    assert part == "*__EN\\-US__*: 1 of 1\nhello"


def test_assemble_engine_reply_renders_per_flavor():
    (part,) = fmt.assemble_engine_reply("3 * 4 = 12", "deepl", flavor="plain")
    assert part == "*__deepl__*: 1 of 1\n3 \\* 4 \\= 12"


def test_assemble_engine_reply_empty_text_yields_no_parts():
    assert fmt.assemble_engine_reply("", "gemini") == []


def test_assemble_plain_reply_keeps_content_raw():
    parts = fmt.assemble_plain_reply("line - one" * 3000, "logs")
    assert len(parts) == 8  # 30000 single-unit chars / 4060 per part
    assert parts[0].startswith("logs: 1 of 8\n")
    assert "-" in parts[0]  # plain parts are never escaped


def test_send_with_fallback_uses_formatted_attempt_first():
    calls = []

    def formatted(text):
        calls.append(("md", text))

    def plain(text):
        calls.append(("plain", text))

    fmt.send_with_fallback("hello", formatted, plain)
    assert calls == [("md", "hello")]


def test_send_with_fallback_resends_same_text_plain_on_failure():
    calls = []

    def formatted(text):
        raise ValueError("bad markdown")

    def plain(text):
        calls.append(("plain", text))

    fmt.send_with_fallback("a_b", formatted, plain)
    assert calls == [("plain", "a_b")]  # text never mutated


def test_send_with_fallback_swallows_double_failure():
    def formatted(text):
        raise ValueError("boom")

    def plain(text):
        raise RuntimeError("boom again")

    fmt.send_with_fallback("text", formatted, plain)  # must not raise
