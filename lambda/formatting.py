"""The single owner of Telegram reply rendering.

Engines no longer pre-escape their answers: they publish **raw** content and
this module turns it into MarkdownV2 messages on the result path (and formats
the bot-originated replies in chatbot.py). Telegram formatting knowledge —
escape sets, the part splitter, the ``*__engine__*`` ``i of n`` headers and the
send-failure fallback — lives here, in one place, instead of being copied into
every engine and into the sender.

Content flavors (extension point)
---------------------------------

Providers return different flavors of text — sometimes plain, sometimes
Markdown-ish — and each may need its own treatment before it is safe to send
with ``MarkdownV2``. A flavor is chosen per result by :func:`resolve_format`:

1. ``ENGINE_FORMAT_OVERRIDES[engine]`` wins when the engine is listed (a
   lambda-side lever to fix an engine's outcome without redeploying engines);
2. otherwise the ``format`` field the engine declared on the wire wins;
3. otherwise the ``markdown`` default applies.

Flavors implemented as renderers in ``_RENDERERS``:

- ``markdown``  — text may carry Markdown intent (code fences, emphasis,
  links): apply the legacy subset escape so intended markup survives and only
  the characters that corrupt MarkdownV2 outside markup are neutralised.
- ``plain``     — literal text: escape the whole MarkdownV2 reserved set so
  nothing is interpreted.
- ``markdownv2``— passthrough for a legacy engine that still pre-escapes
  during migration.

Add a new flavor by adding a renderer to ``_RENDERERS`` and (optionally) a row
to ``ENGINE_FORMAT_OVERRIDES``. The wire field travels on the result payload.

Splitter & fallback
-------------------

:func:`_split_utf16` slices by Telegram's UTF-16 code-unit limit (not Python
code points) so astral characters like emoji are never split and each part
stays within the limit even when it contains many wide characters. Escaping
happens *after* splitting so an escape sequence never straddles two parts.
The fallback in :func:`send_with_fallback` resends the exact same text once
without parse mode — never content-mutating.
"""

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Telegram hard limit is 4096 UTF-16 units; keep legacy headroom for headers.
MAX_MESSAGE_CHARS = 4060

# The legacy subset escape: neutralise only the characters that would break
# MarkdownV2 markup while leaving emphasis/code/link delimiters (* _ ` [ ] ~)
# intact so intended formatting survives.
_SUBSET_PATTERN = re.compile(r"(?<!\|)([.\-+#|{}!=()<>])(?!\|)")

# The full MarkdownV2 reserved set (backslash first is implicit per-char).
_FULL_PATTERN = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

# Format of the reply prefix shown above every engine result part.
_HEADER_TEMPLATE = "*__{label}__*: {part} of {total}"


def _render_markdown(text: str) -> str:
    return _SUBSET_PATTERN.sub(r"\\\1", text)


def _render_plain(text: str) -> str:
    return _FULL_PATTERN.sub(r"\\\1", text)


def _render_passthrough(text: str) -> str:
    return text


_RENDERERS: dict[str, Callable[[str], str]] = {
    "markdown": _render_markdown,
    "plain": _render_plain,
    "markdownv2": _render_passthrough,
}

# Per-engine flavor lever: keyed by the engine label on the result payload.
# An entry here overrides anything the engine declares, so an engine's outcome
# can be re-tuned from the sender side alone.
ENGINE_FORMAT_OVERRIDES: dict[str, str] = {}


def resolve_format(engine: str | None, declared: str | None) -> str:
    """Pick the renderer flavor for one engine result."""
    if engine and engine in ENGINE_FORMAT_OVERRIDES:
        return ENGINE_FORMAT_OVERRIDES[engine]
    if declared in _RENDERERS:
        return declared
    return "markdown"


def format_text(text: str, flavor: str = "markdown") -> str:
    """Render ``text`` for Telegram under the given content flavor."""
    return _RENDERERS.get(flavor, _render_markdown)(text)


def _split_utf16(text: str, max_chars: int) -> list[str]:
    """Slice ``text`` into parts of at most ``max_chars`` UTF-16 units."""
    parts = []
    start = 0
    length = len(text)
    while start < length:
        end = start
        units = 0
        while end < length:
            char_units = 2 if ord(text[end]) > 0xFFFF else 1
            if units + char_units > max_chars:
                break
            units += char_units
            end += 1
        if end == start:  # never stall on a code point wider than the budget
            end = start + 1
        parts.append(text[start:end])
        start = end
    return parts


def assemble_engine_reply(
    text: str,
    engine_label: str,
    *,
    flavor: str = "markdown",
    max_chars: int = MAX_MESSAGE_CHARS,
) -> list[str]:
    """Turn one raw engine result into numbered, rendered Telegram messages.

    Returns one message per part, each with a ``*__engine__*: i of n`` prefix;
    an empty string yields no messages. Each part is rendered separately so an
    escape never straddles a split, and the header label is escaped too.
    """
    label = format_text(engine_label, "markdown")
    parts = _split_utf16(text, max_chars)
    total = len(parts)
    return [
        _HEADER_TEMPLATE.format(label=label, part=i, total=total)
        + "\n"
        + format_text(part, flavor)
        for i, part in enumerate(parts, start=1)
    ]


def assemble_plain_reply(
    text: str,
    header: str,
    *,
    max_chars: int = MAX_MESSAGE_CHARS,
) -> list[str]:
    """Split plain text (no parse mode) into numbered parts, e.g. admin logs."""
    parts = _split_utf16(text, max_chars)
    total = len(parts)
    return [
        f"{header}: {i} of {total}\n{part}"
        for i, part in enumerate(parts, start=1)
    ]


def send_with_fallback(
    text: str,
    formatted_send: Callable[[str], None],
    plain_send: Callable[[str], None],
) -> None:
    """Send ``text`` with MarkdownV2; on failure resend it once as plain text.

    The resend carries the exact same text — never mutated — and failures of
    the plain resend are logged and swallowed (the result queue must not loop).
    ``formatted_send`` / ``plain_send`` are the two attempt strategies with
    chat/reply context already bound.
    """
    try:
        formatted_send(text)
        return
    except Exception as e:
        # A BadRequest from MarkdownV2 is expected for model text; resend plain.
        logger.warning("MarkdownV2 send failed, retrying plain", exc_info=e)
    try:
        plain_send(text)
    except Exception as e:
        logger.error("Plain resend failed too", exc_info=e)
