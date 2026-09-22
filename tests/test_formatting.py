"""Tests for the reply-rendering module.

The whole Telegram formatting pipeline — flavor selection, the model-Markdown
renderer, the LaTeX converter, escaping, splitting, headers and the send-failure
fallback — is exercised here through the module's pure interface; nothing
touches the network, the bot library or Telegram.

The regression that motivates the renderer is pinned by
``test_unbalanced_delimiters_...``, ``test_the_reported_reply_shape_...`` and
``test_the_fallback_text_is_the_raw_answer_...``: model output used to break
MarkdownV2 parsing, and the fallback then showed the user escaped source.
"""

import importlib

import pytest

fmt = importlib.import_module("lambda.formatting")


# --- headers ----------------------------------------------------------------


def test_a_single_part_carries_no_counter():
    # "1 of 1" tells the user nothing, so only a split reply is numbered.
    (part,) = fmt.assemble_engine_reply("hello", "gemini")

    assert part.formatted == "*__gemini__*\nhello"
    assert part.plain == "gemini\nhello"


def test_a_split_reply_is_numbered():
    parts = fmt.assemble_engine_reply("x" * 9000, "gemini")

    assert len(parts) == 3
    assert parts[0].formatted.startswith("*__gemini__*: 1 of 3\n")
    assert parts[1].formatted.startswith("*__gemini__*: 2 of 3\n")
    assert parts[2].formatted.startswith("*__gemini__*: 3 of 3\n")
    assert parts[2].plain.startswith("gemini: 3 of 3\n")


def test_the_header_label_is_escaped():
    (part,) = fmt.assemble_engine_reply("hi", "EN-US")

    assert part.formatted == "*__EN\\-US__*\nhi"
    assert part.plain == "EN-US\nhi"


def test_a_label_cannot_inject_markup_into_the_header():
    (part,) = fmt.assemble_engine_reply("hi", "a*b_c")

    assert part.formatted.startswith("*__a\\*b\\_c__*")


# --- flavors ----------------------------------------------------------------


def test_resolve_format_defaults_to_the_llm_renderer():
    assert fmt.resolve_format(None, None) == "llm"
    assert fmt.resolve_format("gemini", None) == "llm"


def test_resolve_format_prefers_declared_format():
    assert fmt.resolve_format("deepl", "plain") == "plain"


def test_resolve_format_falls_back_on_unknown_declared():
    assert fmt.resolve_format("gemini", "bogus") == "llm"


def test_resolve_format_override_beats_declared():
    fmt.ENGINE_FORMAT_OVERRIDES["gemini"] = "markdown"
    try:
        assert fmt.resolve_format("gemini", "llm") == "markdown"
    finally:
        del fmt.ENGINE_FORMAT_OVERRIDES["gemini"]


def test_plain_flavor_escapes_the_whole_reserved_set():
    rendered = fmt.format_text(
        "3 * 4 = 12 a_b [c] (d) ~e~ `f` #g -h +i |j| {k} !l .m >n \\o",
        "plain",
    )
    for char in "*_[]()~`#+-=|{}!.>\\":
        assert f"\\{char}" in rendered


def test_the_legacy_markdown_flavor_is_still_available():
    # The old rendering is kept so ENGINE_FORMAT_OVERRIDES can pin an engine
    # back to it without a deploy of the engines bundle.
    rendered = fmt.format_text("3 * 4 = 12 #a", "markdown")

    assert rendered == "3 * 4 \\= 12 \\#a"


def test_markdownv2_flavor_passes_text_through():
    raw = "already \\*escaped\\* text"

    assert fmt.format_text(raw, "markdownv2") == raw


# --- the model-Markdown renderer --------------------------------------------


def test_bullets_become_a_character_markdownv2_never_interprets():
    # A model bullet is an unbalanced delimiter waiting to fail the send.
    rendered = fmt.format_text("* one\n* two\n- three\n+ four", "llm")

    assert rendered == "• one\n• two\n• three\n• four"


def test_nested_bullets_keep_their_indentation():
    rendered = fmt.format_text("1. **Total:**\n   * item one", "llm")

    assert rendered == "1\\. *Total:*\n   • item one"


def test_bold_and_italic_are_renormalised():
    assert fmt.format_text("**bold**", "llm") == "*bold*"
    assert fmt.format_text("__bold__", "llm") == "*bold*"
    assert fmt.format_text("*italic*", "llm") == "_italic_"
    assert fmt.format_text("_italic_", "llm") == "_italic_"
    assert fmt.format_text("~~gone~~", "llm") == "~gone~"


def test_an_underscore_inside_a_word_is_not_emphasis():
    assert fmt.format_text("player_hours", "llm") == "player\\_hours"


def test_headings_become_bold():
    assert fmt.format_text("# Heading", "llm") == "*Heading*"
    assert fmt.format_text("### Deeper head", "llm") == "*Deeper head*"


def test_blockquotes_and_rules_are_replaced():
    rendered = fmt.format_text("> quoted\n\n---", "llm")

    assert rendered == "▎ quoted\n\n———"


def test_code_spans_and_fences_are_preserved():
    rendered = fmt.format_text("Use `x` and:\n```py\nprint(1)\n```", "llm")

    assert rendered == "Use `x` and:\n```py\nprint(1)\n```"


def test_code_content_escapes_only_the_two_special_characters():
    rendered = fmt.format_text("```\npath\\to `x`\n```", "llm")

    assert rendered == "```\npath\\\\to \\`x\\`\n```"


def test_a_link_survives():
    rendered = fmt.format_text("See [docs](https://e.com/a_b).", "llm")

    assert rendered == "See [docs](https://e.com/a_b)\\."


def test_unbalanced_delimiters_are_escaped_not_left_to_break_the_send():
    """The failure mode that started this: one stray delimiter fails the send.

    Every delimiter the model left unbalanced must come out escaped, so the
    message parses no matter what the model emitted.
    """
    rendered = fmt.format_text("a * b _ c ` d [ e ~ f\n**unclosed", "llm")

    assert rendered == "a \\* b \\_ c \\` d \\[ e \\~ f\n\\*\\*unclosed"


def test_the_reported_reply_shape_renders_cleanly():
    """The reply that came back to the user as escaped source."""
    raw = (
        "Let $x$ be the hourly rate for one player.\n\n"
        "1. **Calculate total player-hours:**\n"
        "   * 3 players played for 3 hours\n"
    )

    rendered = fmt.format_text(raw, "llm")

    assert "$" not in rendered  # maths delimiters are gone, not shown raw
    assert "Let x be the hourly rate for one player\\." in rendered
    assert "1\\. *Calculate total player\\-hours:*" in rendered
    assert "   • 3 players played for 3 hours" in rendered


def test_trailing_blank_lines_are_trimmed():
    assert fmt.format_text("a\n\n\n\n\nb\n\n\n", "llm") == "a\n\nb"


def test_blank_input_renders_to_nothing():
    assert fmt.format_text("", "llm") == ""
    assert fmt.format_text("   \n  ", "llm") == ""


# --- LaTeX ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (r"\frac{300}{7}", "300/7"),
        (r"\frac{a+b}{c}", "(a+b)/c"),
        (r"\text{Total hours}", "Total hours"),
        (r"3 \times \frac{300}{7} \approx \$128.57", "3 × 300/7 ≈ $128.57"),
        (r"\sqrt[3]{x} + \sqrt{y}", "3√(x) + √(y)"),
        (r"2^{10} + x_1", "2¹⁰ + x₁"),
        (r"\alpha \le \beta \ne \gamma \to \infty", "α ≤ β ≠ γ → ∞"),
        (r"\left( \frac{1}{2} \right)", "( 1/2 )"),
        (r"50\% \text{ of } \$600", "50% of $600"),
        (r"\unknowncmd{x}", "unknowncmdx"),
        (r"a \\ b", "a \n b"),
    ],
)
def test_latex_commands_render_readably(expression, expected):
    assert fmt.latex_to_text(expression) == expected


def test_an_unmappable_subscript_keeps_its_marker():
    # str.translate would pass "n" through and silently yield "yn".
    assert fmt.latex_to_text("y_{n}") == "y_n"
    assert fmt.latex_to_text("y_{n+1}") == "y_{n+1}"


def test_display_math_is_converted():
    rendered = fmt.format_text(r"$$\text{Total hours} = 3 + 4$$", "llm")

    assert rendered == "Total hours \\= 3 \\+ 4"


def test_escaped_dollars_do_not_close_the_maths_span():
    """A live answer writes `$\\$600$`; a naive `[^$]+` regex cannot match it."""
    rendered = fmt.format_text(r"The pool of $\$600$ is divided.", "llm")

    assert rendered == "The pool of $600 is divided\\."


def test_the_real_captured_answer_renders_readably():
    """Lines taken verbatim from a live capture of one maths turn."""
    raw = (
        "$$x = \\frac{600}{14} = \\frac{300}{7} \\approx 42.8571$$\n\n"
        "* **Player 2 (4 hours):** \n"
        "  $$\\text{Share} = 4 \\times \\frac{300}{7} \\approx \\$171.43$$\n"
    )

    rendered = fmt.format_text(raw, "llm")

    assert "x \\= 600/14 \\= 300/7 ≈ 42\\.8571" in rendered
    assert "• *Player 2 \\(4 hours\\):*" in rendered
    assert "Share \\= 4 × 300/7 ≈ $171\\.43" in rendered
    assert "\\frac" not in rendered
    assert "\\text" not in rendered


# --- assembly ---------------------------------------------------------------


def test_the_fallback_text_is_the_raw_answer_not_the_escaped_source():
    """The bug behind the reported garbage: the retry resent escaped markup."""
    (part,) = fmt.assemble_engine_reply("**bold** and 3 * 4", "gemini")

    assert part.formatted == "*__gemini__*\n*bold* and 3 \\* 4"
    assert part.plain == "gemini\n**bold** and 3 * 4"


def test_assemble_engine_reply_empty_text_yields_no_parts():
    assert fmt.assemble_engine_reply("", "gemini") == []
    assert fmt.assemble_engine_reply("   \n ", "gemini") == []


def test_the_raw_text_survives_assembly_unchanged():
    text = "**bold**\n\n$$x = \\frac{1}{2}$$\n\n* bullet"

    parts = fmt.assemble_engine_reply(text, "gemini")

    assert "\n".join(part.plain.split("\n", 1)[1] for part in parts) == text


def test_escaping_expansion_still_fits_the_telegram_limit():
    """Braces expand on escaping, so a fitting raw part can overflow rendered."""
    raw = "{" * 3000

    parts = fmt.assemble_engine_reply(raw, "gemini")

    assert len(parts) > 1
    assert all(fmt._utf16_len(part.formatted) <= fmt.MAX_MESSAGE_CHARS for part in parts)
    assert "".join(part.plain.split("\n", 1)[1] for part in parts) == raw


def test_reply_part_carries_the_raw_text():
    part = fmt.reply_part("Error: x_y")

    assert part.formatted == "Error: x\\_y"
    assert part.plain == "Error: x_y"


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


def test_assemble_plain_reply_keeps_content_raw():
    parts = fmt.assemble_plain_reply("line - one" * 3000, "logs")

    assert len(parts) == 8  # 30000 single-unit chars / 4060 per part
    assert parts[0].startswith("logs: 1 of 8\n")
    assert "-" in parts[0]  # plain parts are never escaped


def test_assemble_plain_reply_single_part_carries_no_counter():
    assert fmt.assemble_plain_reply("one line", "logs") == ["logs\none line"]


# --- send fallback ----------------------------------------------------------


def test_send_with_fallback_uses_formatted_attempt_first():
    calls = []
    part = fmt.reply_part("hello")

    fmt.send_with_fallback(
        part, lambda text: calls.append(("md", text)), lambda text: calls.append(("plain", text))
    )

    assert calls == [("md", "hello")]


def test_send_with_fallback_resends_the_raw_text_on_failure():
    """The retry must show the user their answer, not escaped markup."""
    calls = []
    (part,) = fmt.assemble_engine_reply("**bold**", "gemini")

    def reject(text):
        raise ValueError("bad markdown")

    fmt.send_with_fallback(part, reject, lambda text: calls.append(text))

    assert calls == ["gemini\n**bold**"]


def test_send_with_fallback_swallows_double_failure():
    def reject(text):
        raise ValueError("boom")

    fmt.send_with_fallback(fmt.reply_part("text"), reject, reject)  # must not raise
