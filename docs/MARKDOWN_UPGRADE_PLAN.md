# Markdown / Message-Formatting Upgrade Plan

Status: **proposal only — no legacy code was rewritten.**
Branch context: `bump-deps-remove-claude` (worktree `.worktrees/bump-deps-remove-claude`, commit `22b44f2`).
Date: 2026-09-04 · Owner: maintainer (review + approve before implementation starts)

## 1. Goal

Telegram's messaging API has moved to a **new, improved formatting engine** (Bot API 10.x —
see §2). This repo still formats every message by hand-escaping text for the legacy
`MarkdownV2` parse mode, in four different places, using two slightly-drifting copies of the
same regex. This plan:

1. verifies the python-telegram-bot (PTB) version floor needed for the new engine and keeps
   the dependency aligned (currently `python-telegram-bot>=22.8`, the latest published
   release as of 2026-09-04 — see §2.2);
2. inventories the legacy markdown pipeline and its failure modes (§3–§4);
3. defines the target formatting model and the **planned breaking changes** (§5–§6);
4. gives a phased, reversible migration plan with tests and rollback (§7–§9).

Out of scope: rewriting any legacy formatter *now*. This document is the pre-implementation
contract; implementation happens in follow-up tickets behind a feature flag.

## 2. The new Telegram formatting engine and library support

> Facts below were verified against primary sources in Sep 2026. Full source URLs in §11.

### 2.1 Telegram server side (Bot API)

Telegram's new formatting engine is the **Rich Messages** feature of Bot API 10.x
(primary source: [Bot API changelog](https://core.telegram.org/bots/api-changelog)).

**Bot API 10.1 — 2026-06-11 (verbatim changelog entry):**
> **Rich Messages**
> - Added support for [Rich Messages](/bots/features#rich-messages), allowing bots to send
>   highly structured text and **stream AI-generated replies with seamless rich formatting**.
> - Added the classes RichText… (`RichTextBold`, `RichTextItalic`, `RichTextUnderline`,
>   `RichTextStrikethrough`, `RichTextSpoiler`, `RichTextCode`, `RichTextUrl`, …),
>   `RichText`, `RichBlock…` (`RichBlockParagraph`, `RichBlockSectionHeading`,
>   `RichBlockPreformatted`, `RichBlockList`, `RichBlockTable`, `RichBlockBlockQuotation`,
>   `RichBlockPullQuotation`, `RichBlockCollage`, `RichBlockSlideshow`, `RichBlockDetails`,
>   `RichBlockMap`, `RichBlockAnimation/Photo/Video/Audio/…`, `RichBlockThinking`, …),
>   `RichBlock`, `RichMessage`, `InputRichMessage`, `InputRichMessageContent`,
>   `RichBlockListItem`, `RichBlockTableCell`, `RichBlockCaption` and more.
> - Added the methods **`sendRichMessage`**, **`sendRichMessageDraft`** (stream partial rich
>   messages), and the parameter _rich\_message_ to `editMessageText`.

**Bot API 10.2 — 2026-07-14:** added `InputRichMessage.media` (media referenced from
*"markdown or html formatting"* inside rich messages), the full `InputRichBlock*` family
(lists, preformatted code, tables, quotations, math, collages, slideshows, …) and the
`blocks` field on `InputRichMessage`, plus ephemeral-message support.

**Bot API 10.3 — 2026-08-24:** added rich-message buttons (`RichMessageButton`,
`RichTextButton`), compact tables, expandable blockquotes, documents/blocks and
`tg://document?id=` links.

**Using it (client shape):** there is no `parse_mode` string for the new engine. It is sent via
`sendRichMessage(chat_id, rich_message)` / `sendRichMessageDraft` / `editMessageText(rich_message=…)`
with an `InputRichMessage` whose **exactly-one** of `html`, `markdown`, `blocks` is set
(plus `is_rtl`, `skip_entity_detection`, and `media` referenced from markdown/html via
`tg://photo|video|document|audio?id=…` links). "Rich Markdown" ≈ GitHub Flavored Markdown:
no mandatory escaping of `_ * [ ] ( ) …` outside code — ordinary markdown semantics
(emphasis needs real delimiters, underscores inside words are literal). Classic `MarkdownV2`
escaping rules (escape any `_ * [ ] ( ) ~ \` > # + - = | { } . !` outside code) are unchanged.

**Compatibility & two-tier framing:** none of these releases deprecate or alter the classic
text path — `parse_mode=MarkdownV2|HTML|Markdown` on `sendMessage`/`editMessageText` keeps
working as before. In fact, **no Bot API release through 10.3 (2026-08-24) introduces any new
`parse_mode` value**. Telegram explicitly frames two tiers rather than a replacement:
*regular messages* (MarkdownV2/HTML) remain "the best choice for short text, confirmations,
simple chat flows", while *rich messages* target "highly structured text" and "stream AI-
generated replies with seamless rich formatting" (features page
https://core.telegram.org/bots/features#messages-and-formatting). Telegram's announcement
@BotNews/119 (2026-06-11) is titled "Markup Revolution".

Community-reported rich-message constraints (secondary sources; official grammar section at
`/bots/api#rich-message-formatting-options` is authoritative): ≤32 768 UTF-8 chars,
≤500 blocks incl. nesting, ≤16 nesting levels, ≤50 media, ≤20 table columns; tables,
checklists, `<details>`, LaTeX/math, footnotes and media blocks are native; only inline
formatting inside table cells; client rendering (esp. Telegram Web) still uneven.

### 2.2 python-telegram-bot (client)

- Project floor today: `python-telegram-bot>=22.8`; **22.8 (2026-06-12) is still the newest
  release on PyPI as of 2026-09-04** (verified; no 22.9/23.x, no beta/RC). PTB 22.8 supports
  Bot API 10.0 (`BOT_API_VERSION_INFO=(10,0)`) but **does not implement Rich Messages / Bot
  API 10.1+** (10.1 shipped 2026-06-11, one day *before* 22.8, so it could not be included).
- PTB 22.8 exposes exactly three parse modes: `HTML`, `MARKDOWN` (legacy), `MARKDOWN_V2` —
  **no new markdown mode is shipped** (none exists anywhere in PTB or the Bot API).
- Full Bot API 10.1 support (Rich Messages: `sendRichMessage`, drafts, blocks) is tracked
  upstream in [python-telegram-bot#5261 "Full Support for Bot API 10.1"](https://github.com/python-telegram-bot/python-telegram-bot/issues/5261):
  open, milestone **v23** (due 2026-09-01, still open/overdue); maintainer comment
  2026-06-18 says the 10.1 work is **paused pending major internal refactors**; no PRs
  merged yet. Expect rich-message support only in the future v23 line, not in 22.x patches.
- **Consequence:** there is nothing newer to pin today; the dependency is already at the
  newest published lib version. Until PTB v23 lands 10.1 support we can (a) keep `MarkdownV2` and
  first centralize our own rendering (safe now, §7 Phases 0–3), and (b) treat the native
  Rich-Message switch as a **separate, gated phase** (§7 Phase 4) that waits for the PTB
  release. The only early route to native rich messages would be building raw
  `InputRichMessage` dict payloads through the request layer (workaround suggested in #5261) —
  not recommended for production.

## 3. Legacy markdown pipeline inventory

All bot-visible text ends up in Telegram via one of two paths:

- **Engine answers**: engine Lambda escapes the model/API text to MarkdownV2, `encode_message()`
  (zlib+base64) → SNS result topic → `lambda/results.py` decodes, splits into ≤4060-char parts
  with an `*__<engine>__*: i of n` header, sends each part with
  `parse_mode=MARKDOWN_V2`; on `BadRequest` falls back to plain text, and on any other error
  retries plaintext with `__` removed.
- **Bot-originated text**: help texts, admin `/errors`/`/redrive` output and voice
  transcripts are built/escaped in `lambda/chatbot.py` + `lambda/help_command.py` and sent
  with `MARKDOWN_V2` or plain.

### 3.1 Formatters (legacy code to retire)

| # | Location | What it does | Consumers |
|---|----------|--------------|-----------|
| F1 | `lambda/utils.py:106` `escape_markdown_v2()` (regex at `:18`) | escapes `. - + # \| { } ! = ( ) < >` unless adjacent to `\|` | chatbot voice transcripts, `/redrive` status (local bot replies) |
| F2 | `engines/common_utils.py:54` `escape_markdown_v2()` (regex at `:14`) | identical copy of F1 | `deepl_tr.py`, `monsterapi.py`, `monsterapi_result.py` |
| F3 | `engines/gemini.py:115` `__as_markdown()` | bespoke “strip emphasis, then escape” regex (`*` collapsing, subset escaping) | `gemini.py` answers |
| F4 | `lambda/utils.py:126` `split_long_message()` | char-slicing at 4060 + injects `"<header>: i of n"` | results.py engine headers, chatbot `/errors` logs |
| F5 | inline `f"*__{engine}__*"` header (`lambda/results.py:37`) | builds MarkdownV2 underline/bold label | results.py |
| F6 | static escaped strings in `lambda/help_command.py` (raw `r"""..."""` blocks) and a few `chatbot.py` replies | hand-written MarkdownV2 docs/errors | `/start /help …` |

### 3.2 Actual behavior (measured 2026-09-04, Python 3.14)

Input `Markdown: *bold* _it_ __u__ ~s~ ||s|| code `x` [lnk](https://a.b) a_b c*d 5<6 >4 #h -d +p =e |p| {b} (p) [b] !x .y`

- `escape_markdown_v2` (F1 == F2, output identical) escapes `. ( ) < > # - + = | { } !`
  **but not** `* _ ~ \` [ ]` → `*bold*`, `_it_`, `__u__`, `~s~`, `||s||`, `` `x` ``, `[lnk]`
  survive unescaped and are interpreted (or break) MarkdownV2 at render time.

## 4. Why the legacy approach must change (risk register)

- R1 **Incomplete/incorrect escaping set.** MarkdownV2 reserves `_ * [ ] ( ) ~ \` > # + - = | { } . !`
  outside code spans; the helpers escape a subset and leave emphasis/link/code delimiters
  raw. LLM answers containing unbalanced `*`/`_`/`` ` `` regularly produce `BadRequest` →
  fallback to plaintext (losing all formatting), or accidental formatting.
- R2 **Two copies of the same regex** (`lambda/utils.py` vs `engines/common_utils.py`) can
  drift; today they are behaviour-identical.
- R3 **Double-processing across hops.** Voice transcripts are escaped once in `chatbot.py`
  and the *same text* is also fanned out to engines, which escape it a second time for the
  result path (R3a). Engine answers are pre-escaped *and stored pre-escaped* in
  DynamoDB conversation history (R3b): switching formatting later makes stored history
  inconsistent and forces re-formatting or a context reset.
- R4 **Splitter is format-blind.** `split_long_message` slices Python characters at 4060,
  which can cut surrogate pairs, split code fences/links/spoilers across parts, and inject the
  `i of n` header mid-markup. Telegram limits are in UTF-16 code units.
- R5 **Fallback logic hides errors.** `results.py` swallows `BadRequest` and re-sends
  unformatted; a generic exception re-sends with `text.replace("__", " ")` — content-mutating.
- R6 **Formatting is done in 6 places** instead of one pipeline, making a consistent switch to
  a new engine impossible without centralization first.
- R7 **Static MarkdownV2 text** (help, `/redrive`, etc.) is hand-escaped; any escaping-rule
  change on the server (or a new parse mode) silently breaks rendering.

## 5. Target formatting model (design proposal, not implemented)

1. **One formatter module** (e.g. `lambda/formatting.py`, mirrored to `engines/` only via the
   existing shared lock, or better: content stays **unformatted** in the SNS envelope and
   formatting happens once, in the result path).
2. **Content + style instead of pre-rendered text**: engine envelope carries the raw answer
   plus a declared content type (`text/markdown`, `text/plain`, later `rich_message`); the
   sender renders — no double escaping, history stores raw content.
3. Renderer modes selected by capability: legacy `MarkdownV2` renderer now; native rich /
   improved-markdown renderer when PTB supports Bot API 10.1 (§2.2), feature-detected.
4. Splitter becomes entity/markup-aware (never cuts inside code/link/entity, respects UTF-16
   limit 4096 with headroom for the `i of n` header).
5. Plain-text fallback remains explicit and non-mutating.

## 6. Planned breaking changes (for users of the bot and of the code)

### 6.1 User-visible
- B1 Rendering differences: engine header `*__gemini__*` and bold/underline emphasis may
  disappear or change if we drop `MarkdownV2`; code blocks/links from models will finally
  render correctly (previously often escaped or broken).
- B2 Stored conversation history (DynamoDB `user-conversations`, `user-context`): previously
  saved pre-escaped text; after migration it holds raw content → `/reset` semantics unchanged,
  but old sessions may render inconsistently until naturally expired (context TTL 60d) or
  reset.
- B3 Split messages: part count/headers (`i of n`) may change slightly when splits become
  markup-aware.

### 6.2 Code-facing
- B4 **Removed**: `escape_markdown_v2` (both copies), `__as_markdown`, `split_long_message`
  header-format string, `*__<engine>__*` convention, raw MarkdownV2 strings in
  `help_command.py` are replaced by a formatter API.
- B5 SNS `result-ai-topic` message contract: `response` becomes raw content + `format` field
  (old field kept during transition with an explicit `format: "markdownv2"` marker).
- B6 Engine→DB writes: engines no longer escape before storing.
- B7 PTB floor will rise to the release that supports the chosen native engine when it lands
  upstream (tracked via §2.2); keep `>=22.8` until then.
- B8 `constants.ParseMode.MARKDOWN_V2` call sites (chatbot.py, help_command.py, results.py)
  migrate to the formatter; PTB `send_message` calls may switch to entity-based sends.

## 7. Phased migration

- **Phase 0 — observe (1 sprint).** Log parse-mode `BadRequest` fallback rate and capture
  samples; snapshot current reply appearance (golden chat captures) per engine.
- **Phase 1 — centralize rendering (no visible change).** New `formatting` module +
  `parse-markdown-to-entities/rich` implementation; golden-file unit tests; keep old helpers
  but route everything through the new module in “MarkdownV2-compat mode”.
- **Phase 2 — raw-content envelope.** Engines stop pre-escaping; results render
  (compat mode). Feature flag `markup.migrate_envelope` (default on after soak in dev).
- **Phase 3 — flip renderer per surface** behind flags: `markup.renderer.gemini` →
  `…monsterapi` → `…deepl` → help/static → voice; each with a canary chat + alarm on
  `BadRequest`.
- **Phase 4 — adopt the native engine (gated).** When PTB ships Bot API 10.1 support
  (milestone v23 upstream):
  1. **Strip first, then re-format** — legacy MarkdownV2-escaped text (backslash-prefixed
     `_ * [ ] …`) fed into Rich Markdown renders literal backslashes; the envelope change in
     Phase 2 (raw content) is what makes this safe.
  2. Two-tier routing per official framing: short/simple replies keep the MarkdownV2
     renderer; structured/long/AI output (headings, tables, task lists, LaTeX, media
     blocks, >4096 chars) goes through `InputRichMessage` (`markdown` or `blocks`;
     exactly-one semantics, `skip_entity_detection` for LLM URLs/mentions).
  3. Renderer v2 builds/validates against the rich-message constraints (§2.1 limits);
     drafts/streaming behaviour validated (see nanobot #5516, hermes-agent #46009 —
     editing a rich message with plain text drops formatting).
- **Phase 5 — delete legacy.** Remove F1–F6 + fallback quirks; history TTL handles old rows;
  update README/help.
- **Exit criteria per phase:** zero net `BadRequest` increase; golden diffs reviewed;
  one-week soak in `dev` stage before `prod`.

## 8. Test strategy
- Unit/golden tests: representative + adversarial inputs (unbalanced `*`, nested lists,
  code fences, links, emoji, very long lines, tables) → expected entity/plain output.
- Property tests: formatter(escape-inverse) invariants; no lone-surrogate splits.
- Integration (dev token, private chat): end-to-end through the SNS pipeline; assert
  rendered entity types on `Message.entities`; assert no `BadRequest`.
- Regression: compare current vs migrated replies for identical prompts (diff report).
- Load: multi-part (>4060) messages with and without code fences.

## 9. Rollback
- Feature flags (§7) default to previous behaviour during rollout; a flag-off restores the
  legacy pipeline untouched until Phase 5 deletes it. DB reset knob (`/reset`) documented for
  users who hit B2 issues.

## 10. Open questions for the maintainer
1. Which surfaces must keep the `*__engine__*` label style?
2. Do we ever want tables/inline media from model output (Rich Messages), or only richer
   text/entity formatting?
3. History migration: leave old rows to TTL, or provide a one-off re-format job?
4. Should `/errors`/`/redrive` (admin) move off MarkdownV2 too, or plain-text-only?

## 11. Sources & references

Primary:
- Telegram Bot API changelog — https://core.telegram.org/bots/api-changelog
  (entries: **Bot API 10.3** 2026-08-24; **Bot API 10.2** 2026-07-14; **Bot API 10.1**
  2026-06-11 — Rich Messages; earlier entries confirm no deprecation of MarkdownV2/HTML)
- Telegram Bot API reference — https://core.telegram.org/bots/api
  (anchors: `#formatting-options`, `#markdownv2-style`, `#rich-markdown-style`,
  `#rich-html-style`, `#rich-message-formatting-options`, `#inputrichmessage`,
  `#sendrichmessage`, `#sendrichmessagedraft`)
- Telegram Bot Features: messages & formatting / Rich Messages —
  https://core.telegram.org/bots/features#messages-and-formatting and `#rich-messages`
- Telegram announcements: @BotNews/119 ("Markup Revolution", 2026-06-11), /120, /121 —
  https://t.me/BotNews/119

Library:
- python-telegram-bot releases — https://github.com/python-telegram-bot/python-telegram-bot/releases
- PyPI — https://pypi.org/pypi/python-telegram-bot/ (22.8 = latest, verified 2026-09-04)
- PTB issue "Full Support for Bot API 10.1" — https://github.com/python-telegram-bot/python-telegram-bot/issues/5261
- aiogram ≥3.30.0 already implements `send_rich_message`/`InputRichMessage`/`InputRichBlock*`
  (reference shape of the new API until PTB catches up):
  https://github.com/aiogram/aiogram/blob/dev-3.x/aiogram/methods/send_rich_message.py

Ecosystem trackers & community (secondary; some rich-grammar limits unverified against the
official page, which is too large for a single fetch):
- https://github.com/HKUDS/nanobot/issues/4422
- https://github.com/HKUDS/nanobot/issues/5516
- https://github.com/NousResearch/hermes-agent/issues/46234 (and #46009 — editing a rich
  message with plain text drops formatting)
- https://upppp.jp/app-service/20260616-192806/ · https://habr.com/ru/articles/1046786/
