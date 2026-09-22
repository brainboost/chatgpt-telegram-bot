"""The single owner of Telegram reply rendering.

Engines publish **raw** content and this module turns it into MarkdownV2
messages on the result path (and formats the bot-originated replies in
chatbot.py). Telegram formatting knowledge — escaping, the model-Markdown
renderer, the LaTeX converter, the part splitter, the ``*__engine__*`` headers
and the send-failure fallback — lives here, in one place, instead of being
copied into every engine and into the sender.

Why model output needs a real converter
---------------------------------------

An LLM answer is Markdown-*ish* and, for maths, LaTeX. It is not valid
MarkdownV2, and Telegram rejects a whole message when its markup does not
parse: one unbalanced ``*`` or ``_``, one stray ``[``, and the send fails.
The previous approach — escape a subset of characters and hope the model's
delimiters happened to balance — produced exactly that failure, and the
fallback then showed the user the *escaped source* (``*__gemini__*: 1 of 1``,
``player\\-hours``) instead of their answer.

So :func:`_render_llm` parses the model's Markdown and *re-emits* markup this
module generated itself, which is valid by construction:

- emphasis, code spans, fences, links, headings and blockquotes are recognised;
- list bullets become ``•``, which is not a MarkdownV2 metacharacter, so a
  bullet can never be mistaken for an emphasis delimiter;
- LaTeX is converted to readable text (:func:`latex_to_text`): ``\\frac{300}{7}``
  becomes ``300/7``, ``\\times`` becomes ``×``, ``\\text{Total hours}`` becomes
  ``Total hours`` and ``x^2`` becomes ``x²`` — Telegram renders no maths, so
  leaving the source would only show backslashes to the user;
- every character that is *not* part of markup we emitted is escaped, so text
  the model meant literally stays literal.

Content flavors (extension point)
---------------------------------

A flavor is chosen per result by :func:`resolve_format`:

1. ``ENGINE_FORMAT_OVERRIDES[engine]`` wins when the engine is listed (a
   lambda-side lever to fix an engine's outcome without redeploying engines);
2. otherwise the ``format`` field the engine declared on the wire wins;
3. otherwise :data:`DEFAULT_FLAVOR` applies.

Flavors implemented as renderers in ``_RENDERERS``:

- ``llm``        — model Markdown plus LaTeX; the default (see above).
- ``plain``      — literal text: escape everything, interpret nothing.
- ``markdown``   — the legacy subset escape, kept so
  ``ENGINE_FORMAT_OVERRIDES`` can pin an engine back to the old behaviour.
- ``markdownv2`` — passthrough for an engine that pre-escapes its own markup.

Splitter & fallback
-------------------

:func:`_split_utf16` slices by Telegram's UTF-16 code-unit limit (not Python
code points) so astral characters like emoji are never split. Rendering happens
per part, so a generated escape sequence never straddles two parts; because
escaping *expands* text, :func:`_render_with_budget` halves a part that would
overflow once rendered.

Each assembled part carries two forms: ``formatted`` for the MarkdownV2
attempt, and ``plain`` — the raw content with an unmarked header — for
:func:`send_with_fallback`, which retries with it when parsing fails. The
fallback therefore degrades to the user's answer, never to escaped source.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import providers

logger = logging.getLogger(__name__)

# Telegram hard limit is 4096 UTF-16 units; keep legacy headroom for headers.
MAX_MESSAGE_CHARS = 4060

# Room kept for the ``*__label__*: i of n`` header when splitting, so content
# plus header still fits the budget above.
_HEADER_RESERVE = 64

# The same constant the engines declare on the wire: see providers.py for why it
# is shared rather than written twice.
DEFAULT_FLAVOR = providers.DEFAULT_CONTENT_FLAVOR

# The full MarkdownV2 reserved set, escaped character by character.
_MDV2_RESERVED = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

# Inside code and pre entities only the backtick and the backslash are special.
_CODE_RESERVED = re.compile(r"([`\\])")

# Header shown above every engine result part, formatted and plain.
_HEADER_TEMPLATE = "*__{label}__*"
_HEADER_COUNT = "{header}: {part} of {total}"


def _escape(text: str) -> str:
    """Escape every MarkdownV2 metacharacter in ``text``."""
    return _MDV2_RESERVED.sub(r"\\\1", text)


def _escape_code(text: str) -> str:
    """Escape the only two characters that are special inside code entities."""
    return _CODE_RESERVED.sub(r"\\\1", text)


# --- LaTeX ------------------------------------------------------------------

# Commands replaced by a single character. Telegram renders no maths, so the
# goal is the closest readable glyph rather than typographic fidelity.
_LATEX_SYMBOLS: dict[str, str] = {
    # operators and relations
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "approx": "≈", "neq": "≠", "ne": "≠", "equiv": "≡", "sim": "∼",
    "le": "≤", "leq": "≤", "leqslant": "≤", "ge": "≥", "geq": "≥",
    "geqslant": "≥", "ll": "≪", "gg": "≫", "propto": "∝",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩",
    "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬",
    "land": "∧", "lor": "∨", "therefore": "∴", "because": "∵",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "leftrightarrow": "↔",
    "Leftrightarrow": "⇔", "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    # big operators, sets and calculus
    "sum": "Σ", "prod": "Π", "int": "∫", "oint": "∮", "partial": "∂",
    "nabla": "∇", "infty": "∞", "emptyset": "∅", "varnothing": "∅",
    # delimiters
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "vert": "|", "Vert": "‖",
    # misc
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "circ": "∘",
    "degree": "°", "deg": "°", "star": "⋆", "ast": "∗", "prime": "′",
    "oplus": "⊕", "otimes": "⊗", "square": "□", "checkmark": "✓",
    "bullet": "•", "S": "§", "P": "¶", "copyright": "©", "pounds": "£",
    "euro": "€", "yen": "¥", "cent": "¢",
    # Greek, lowercase then uppercase
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
    "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ",
    "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
}

# Commands that render their argument as ordinary text.
_LATEX_TEXT_COMMANDS = frozenset(
    {
        "text", "textrm", "textit", "textbf", "mathrm", "mathbf", "mathit",
        "mathsf", "mathtt", "operatorname", "mbox", "hbox", "textnormal",
    }
)
_LATEX_FRACTIONS = frozenset({"frac", "dfrac", "tfrac", "cfrac"})
# Commands that add horizontal space.
_LATEX_SPACES: dict[str, str] = {
    "quad": "  ", "qquad": "    ", "enspace": " ", "thinspace": " ",
    "negthinspace": "", "hspace": " ",
}
# Commands that only size the delimiter that follows them.
_LATEX_SIZERS = frozenset(
    {
        "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
        "Bigl", "Bigr", "biggl", "biggr",
    }
)
# A backslash followed by one of these means the literal character.
_LATEX_ESCAPES: dict[str, str] = {
    "\\": "\n", " ": " ", ",": "", ";": "", ":": "", "!": "", "-": "",
    "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}",
    "|": "‖", "'": "′", '"': "″", "/": "",
}
# Scripts are only converted when every character has a Unicode glyph, since
# str.translate would otherwise pass an unmapped character through unchanged and
# silently drop the marker (``y_n`` becoming ``yn``).
_SUPERSCRIPTS: dict[str, str] = dict(
    zip("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ", strict=True)
)
_SUBSCRIPTS: dict[str, str] = dict(
    zip("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", strict=True)
)

# An atom that mixes operators needs parentheses when it becomes a numerator or
# denominator: \frac{a+b}{c} must not read as a + b/c.
_MIXED_ATOM = re.compile(r"[+\-×÷·*/=\s]")


def _read_group(text: str, start: int) -> tuple[str, int]:
    """Contents of the ``{...}`` group at ``start``, and the index after it."""
    if start >= len(text) or text[start] != "{":
        return "", start
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return text[start + 1 :], len(text)  # unbalanced: take the remainder


def _read_atom(text: str, start: int) -> tuple[str, int]:
    """The next rendered atom: a braced group, a command, or one character."""
    if start >= len(text):
        return "", start
    if text[start] == "{":
        raw, end = _read_group(text, start)
        return latex_to_text(raw), end
    if text[start] == "\\":
        return _read_command(text, start)
    return text[start], start + 1


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index] == " ":
        index += 1
    return index


def _as_term(atom: str) -> str:
    """Parenthesise an atom that would otherwise rebind around a fraction bar."""
    if atom and _MIXED_ATOM.search(atom):
        return f"({atom})"
    return atom


def _read_command(text: str, start: int) -> tuple[str, int]:
    """Render the LaTeX command at ``start`` and return the index after it."""
    index = start + 1
    if index >= len(text):
        return "", index
    if not text[index].isalpha():
        char = text[index]
        return _LATEX_ESCAPES.get(char, char), index + 1

    end = index
    while end < len(text) and text[end].isalpha():
        end += 1
    name = text[index:end]

    if name in _LATEX_FRACTIONS:
        numerator, after = _read_atom(text, _skip_spaces(text, end))
        denominator, after = _read_atom(text, _skip_spaces(text, after))
        return f"{_as_term(numerator)}/{_as_term(denominator)}", after
    if name == "sqrt":
        degree = ""
        root = _skip_spaces(text, end)
        if text[root : root + 1] == "[":
            close = text.find("]", root)
            if close != -1:
                degree = f"{text[root + 1 : close]}√"
                end = close + 1
        radicand, after = _read_atom(text, _skip_spaces(text, end))
        return f"{degree or '√'}({radicand})", after
    if name in _LATEX_TEXT_COMMANDS:
        if text[end : end + 1] == "{":
            raw, after = _read_group(text, end)
            # Stripped because the source usually spaces the command itself:
            # ``14 \text{ hours}`` must not become ``14  hours``.
            return latex_to_text(raw).strip(), after
        return "", end
    if name in _LATEX_SPACES:
        return _LATEX_SPACES[name], end
    if name in _LATEX_SIZERS:
        following = _skip_spaces(text, end)
        character = text[following : following + 1]
        if character in {"", "{", "\\"}:
            return "", end
        return ("" if character == "." else character), following + 1
    if name in _LATEX_SYMBOLS:
        return _LATEX_SYMBOLS[name], end
    # Unknown command: keep the name so nothing is silently dropped.
    return name, end


def latex_to_text(expression: str) -> str:
    """Convert LaTeX maths to readable plain text.

    Unknown commands lose only their backslash, so an unsupported construct
    degrades to something readable instead of vanishing.
    """
    out: list[str] = []
    index = 0
    length = len(expression)
    while index < length:
        char = expression[index]
        if char == "\\":
            rendered, index = _read_command(expression, index)
            out.append(rendered)
        elif char == "{":
            raw, index = _read_group(expression, index)
            out.append(latex_to_text(raw))
        elif char == "}":
            index += 1  # stray closer
        elif char == "^":
            rendered, index = _read_script(
                expression, index + 1, _SUPERSCRIPTS, "^"
            )
            out.append(rendered)
        elif char == "_":
            rendered, index = _read_script(
                expression, index + 1, _SUBSCRIPTS, "_"
            )
            out.append(rendered)
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _read_script(
    text: str, start: int, table: dict[str, str], marker: str
) -> tuple[str, int]:
    """Render ``^``/``_`` plus its atom, as Unicode when the glyphs exist."""
    atom, after = _read_atom(text, start)
    if not atom:
        return "", after
    if all(char in table for char in atom):
        return "".join(table[char] for char in atom), after
    if len(atom) == 1:
        return f"{marker}{atom}", after
    return f"{marker}{{{atom}}}", after


# --- model Markdown -> MarkdownV2 -------------------------------------------

_FENCE_RE = re.compile(r"^\s*`{3,}\s*([A-Za-z0-9_+#-]*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[*+-]\s+(.*)$")
_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]*)\)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _wrap(opener: str, closer: str, inner: str) -> str | None:
    """Wrap ``inner`` in ``opener``/``closer``, or return None when invalid.

    MarkdownV2 rejects an entity whose content starts or ends with whitespace,
    so the delimiters stay literal in that case rather than failing the send.
    """
    if not inner or inner != inner.strip():
        return None
    return f"{opener}{inner}{closer}"


def _can_open_underscore(text: str, index: int) -> bool:
    """``_`` opens emphasis only at a word boundary, as in CommonMark."""
    return index == 0 or not text[index - 1].isalnum()


def _emphasis(
    text: str, start: int, delimiter: str, telegram: str
) -> tuple[str, int]:
    """Render one emphasis run, or return its literal delimiters."""
    opening_end = start + len(delimiter)
    end = text.find(delimiter, opening_end)
    if end > opening_end:
        inner = _inline(text[opening_end:end])
        wrapped = _wrap(telegram, telegram, inner)
        if wrapped is not None:
            return wrapped, end + len(delimiter)
    return _escape(delimiter), opening_end


def _find_math_end(text: str, start: int, delimiter: str) -> int | None:
    """End of a maths span, skipping escaped characters such as ``\\$``."""
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text.startswith(delimiter, index):
            return index
        index += 1
    return None


def _escape_url(url: str) -> str:
    """Inside a link target only ``)`` and ``\\`` are special."""
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _inline(text: str) -> str:
    """Render one line's inline markup, escaping everything else."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "`":
            end = text.find("`", index + 1)
            if end > index + 1:
                out.append(f"`{_escape_code(text[index + 1 : end])}`")
                index = end + 1
                continue
        elif text.startswith("$$", index) or char == "$":
            opening = 2 if text.startswith("$$", index) else 1
            end = _find_math_end(text, index + opening, "$" * opening)
            if end is not None and end > index + opening:
                maths = latex_to_text(text[index + opening : end])
                out.append(_escape(maths))
                index = end + opening
                continue
        elif text.startswith("\\(", index) or text.startswith("\\[", index):
            closer = "\\)" if text[index + 1] == "(" else "\\]"
            end = text.find(closer, index + 2)
            if end > index + 2:
                maths = latex_to_text(text[index + 2 : end])
                out.append(_escape(maths))
                index = end + 2
                continue
        elif text.startswith("**", index) or text.startswith("__", index):
            delimiter = text[index : index + 2]
            rendered, index = _emphasis(text, index, delimiter, "*")
            out.append(rendered)
            continue
        elif text.startswith("~~", index):
            rendered, index = _emphasis(text, index, "~~", "~")
            out.append(rendered)
            continue
        elif char == "*" or (char == "_" and _can_open_underscore(text, index)):
            rendered, index = _emphasis(text, index, char, "_")
            out.append(rendered)
            continue
        elif char == "[" and (link := _LINK_RE.match(text, index)):
            out.append(f"[{_escape(link.group(1))}]({_escape_url(link.group(2))})")
            index = link.end()
            continue
        out.append(_escape(char))
        index += 1
    return "".join(out)


def _render_line(line: str) -> str:
    """Render one non-code line: block prefix, then inline markup."""
    line = line.rstrip()
    if not line.strip():
        return ""
    stripped = line.lstrip()
    if _RULE_RE.match(stripped):
        return "———"
    if heading := _HEADING_RE.match(stripped):
        body = _inline(heading.group(2).strip())
        return _wrap("*", "*", body) or body
    if quote := _QUOTE_RE.match(stripped):
        return f"▎ {_inline(quote.group(1))}"
    if bullet := _BULLET_RE.match(line):
        # A bullet becomes •, which MarkdownV2 never reads as markup.
        return f"{bullet.group(1)}• {_inline(bullet.group(2))}"
    return _inline(line)


def _render_llm(text: str) -> str:
    """Render model Markdown (plus LaTeX) as valid MarkdownV2."""
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        if fence := _FENCE_RE.match(lines[index]):
            language = fence.group(1)
            index += 1
            block: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                block.append(_escape_code(lines[index]))
                index += 1
            index += 1  # consume the closing fence
            body = "\n".join(block)
            out.append(f"```{language}\n{body}\n```")
            continue
        out.append(_render_line(lines[index]))
        index += 1
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(out)).strip()


def _render_plain(text: str) -> str:
    return _escape(text)


_LEGACY_SUBSET = re.compile(r"(?<!\|)([.\-+#|{}!=()<>])(?!\|)")


def _render_legacy_markdown(text: str) -> str:
    """The pre-converter escape: neutralise a subset, trust the delimiters.

    Kept only so ``ENGINE_FORMAT_OVERRIDES`` can pin an engine back to the old
    rendering without a code change.
    """
    return _LEGACY_SUBSET.sub(r"\\\1", text)


def _render_passthrough(text: str) -> str:
    return text


_RENDERERS: dict[str, Callable[[str], str]] = {
    "llm": _render_llm,
    "plain": _render_plain,
    "markdown": _render_legacy_markdown,
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
    return DEFAULT_FLAVOR


def format_text(text: str, flavor: str = DEFAULT_FLAVOR) -> str:
    """Render ``text`` for Telegram under the given content flavor."""
    return _RENDERERS.get(flavor, _render_llm)(text)


# --- assembly ---------------------------------------------------------------


@dataclass(frozen=True)
class ReplyPart:
    """One Telegram message: the formatted attempt and its plain twin.

    ``plain`` exists so a failed MarkdownV2 send can be retried with content the
    user can actually read, instead of the escaped source of ``formatted``.
    """

    formatted: str
    plain: str


def reply_part(text: str, *, flavor: str = DEFAULT_FLAVOR) -> ReplyPart:
    """One headerless message plus the plain text to fall back to."""
    return ReplyPart(formatted=format_text(text, flavor), plain=text)


def _utf16_len(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


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


def _render_with_budget(
    raw: str, flavor: str, budget: int, out: list[tuple[str, str]]
) -> None:
    """Render ``raw``, halving it until each rendered piece fits ``budget``.

    Escaping expands text — LaTeX braces and operators are the worst case — so a
    raw part that fits the raw budget can still overflow once rendered. Each
    entry pairs the rendered text with the raw slice it came from, which is what
    the send fallback retries with.
    """
    rendered = format_text(raw, flavor)
    if _utf16_len(rendered) <= budget or len(raw) <= 1:
        out.append((raw, rendered))
        return
    middle = len(raw) // 2
    _render_with_budget(raw[:middle], flavor, budget, out)
    _render_with_budget(raw[middle:], flavor, budget, out)


def _headers(label: str, total: int) -> list[tuple[str, str]]:
    """Formatted and plain header for each part.

    A single part carries no counter: ``1 of 1`` says nothing, so only a reply
    that was actually split is numbered.
    """
    formatted = _HEADER_TEMPLATE.format(label=_escape(label))
    if total == 1:
        return [(formatted, label)]
    return [
        (
            _HEADER_COUNT.format(header=formatted, part=index, total=total),
            _HEADER_COUNT.format(header=label, part=index, total=total),
        )
        for index in range(1, total + 1)
    ]


def assemble_engine_reply(
    text: str,
    engine_label: str,
    *,
    flavor: str = DEFAULT_FLAVOR,
    max_chars: int = MAX_MESSAGE_CHARS,
) -> list[ReplyPart]:
    """Turn one raw engine result into numbered, rendered Telegram messages.

    Returns one :class:`ReplyPart` per message; an empty or blank result yields
    no messages. Each part is rendered on its own so a generated escape never
    straddles a split, and the header label is escaped too.
    """
    if not text.strip():
        return []
    budget = max(max_chars - _HEADER_RESERVE, 1)
    pieces: list[tuple[str, str]] = []
    for raw_part in _split_utf16(text, budget):
        _render_with_budget(raw_part, flavor, budget, pieces)
    headers = _headers(engine_label, len(pieces))
    return [
        ReplyPart(formatted=f"{header[0]}\n{rendered}", plain=f"{header[1]}\n{raw}")
        for header, (raw, rendered) in zip(headers, pieces, strict=True)
    ]


def assemble_plain_reply(
    text: str,
    header: str,
    *,
    max_chars: int = MAX_MESSAGE_CHARS,
) -> list[str]:
    """Split plain text (no parse mode) into numbered parts, e.g. admin logs."""
    parts = _split_utf16(text, max_chars)
    if len(parts) == 1:
        return [f"{header}\n{parts[0]}"]
    total = len(parts)
    return [
        f"{header}: {i} of {total}\n{part}"
        for i, part in enumerate(parts, start=1)
    ]


async def send_with_fallback(
    part: ReplyPart,
    formatted_send: Callable[[str], Awaitable[None]],
    plain_send: Callable[[str], Awaitable[None]],
) -> None:
    """Send ``part.formatted`` with MarkdownV2; on failure send ``part.plain``.

    The retry carries the *raw* content, so a parse failure degrades to readable
    text rather than to escaped markup. A failing plain send is logged and
    swallowed (the result queue must not loop).

    Async because the caller has to ``await`` it on the loop its send callables
    run on: the fallback is a *second* request through the same bot, and PTB's
    pooled keep-alive connections belong to the loop that created them. Starting a
    new loop per attempt trips over the previous one while closing its connection
    (``RuntimeError: Event loop is closed``), which is how the retry came to fail
    after a send that had already failed.
    """
    try:
        await formatted_send(part.formatted)
        return
    except Exception as e:
        # A BadRequest from MarkdownV2 is expected for model text; resend plain.
        logger.warning("MarkdownV2 send failed, retrying unformatted", exc_info=e)
    try:
        await plain_send(part.plain)
    except Exception as e:
        logger.error("Plain resend failed too", exc_info=e)
