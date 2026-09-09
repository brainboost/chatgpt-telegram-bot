# Domain Glossary (CONTEXT.md)

Domain vocabulary for this project. When you name a module, a seam or a
concept in code, docs or reviews, use these terms — not synonyms ("service",
"component", "agent").

## The bot in one paragraph

A serverless Telegram bot. The bot Lambda (`lambda/` bundle) turns Telegram
updates into typed **requests**, publishes them on a request topic and SNS/SQS
routes each to a **provider** worker (the `engines/` bundle — one Lambda per
provider), which calls an external API, keeps per-user **conversation
context**, and publishes **engine results** back to a result topic; a result
handler Lambda turns each result into Telegram **reply parts** and sends them.
Chat providers form a **failover chain** (gemini → qwen → llama): a request
starts at the user's conversation-start provider and, when that provider
fails, the worker re-publishes the request to the next one in the chain.

## Terms

- **Provider** — one selectable backend capability the user can enable and use
  (chat, translation, image generation). Conceptually: an external API (Gemini,
  Ollama Cloud, DeepL, Ideogram) plus its bot-side worker. A provider has an
  **id** ("gemini", "llama", "qwen", "deepl", "ideogram") that is its identity
  on the wire, in user configuration, in context rows and in the
  `*__<id>__*` reply header, and a **kind** describing what it does
  (chat / translate / image). *Legacy term: "engine" — see the ADR.*

- **Request / request message** — the typed envelope the bot publishes to a
  provider worker (`lambda/request_message.py`). Kinds: `text`, `command`
  (e.g. `/reset`), `translate`, `ideogram`. Carries the user/chat/message
  identity and the raw text; routing happens by SNS message attributes, not in
  the body.

- **Engine result / result** — a provider worker's published answer on the
  result topic. Raw content plus a **flavor** declaration (`markdown`,
  `plain`, legacy `markdownv2`) and the provider id; the result handler renders
  it into Telegram reply parts (`lambda/formatting.py`, `lambda/results.py`).

- **Reply parts / reply rendering** — Telegram-ready messages assembled from a
  raw result: flavor-based escaping, UTF-16-safe splitting and the
  `*__<id>__*: i of n` headers, with one plain-text resend as the send
  fallback.

- **User configuration** — per-user persisted settings in the
  `user-configurations` table: the conversation-start provider (legacy
  `engines` field — a list whose first id starts the failover chain and is
  kept across sessions so follow-ups stay on the same model), translation
  languages, tone/style.

- **Conversation context / context** — the per-(user × chat) × provider
  history stored in `user-context` rows with a TTL; a **session** is the
  runtime object that loads one context for a request
  (`engines/user_context.py`), and providers that keep sessions see their last
  turns as prompt history.

- **Provider worker / session runtime** — the bot-side Lambda role that
  adapts one provider API (`engines/gemini.py`, `ollama.py`, `deepl_tr.py`,
  `ideogram_result.py`) through the shared engine-session runtime
  (`engines/session.py`): intake → context → answer → save → publish.

## Naming rules

- New code, modules and docs: say **provider**, not engine.
- Legacy surfaces keep the word *engine* on purpose (wire attribute `engines`,
  stored config key, result `engine` field, `OLLAMA_ENGINE`, bundle/infra
  names) — see `docs/adr/0001-provider-vocabulary-no-blanket-rename.md`.
  The user-config `engines` value now means *conversation-start provider*.
- The provider catalog (`providers.py`) is the single source of provider
  identity; do not hardcode provider ids elsewhere.
