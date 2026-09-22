"""Provider catalog: identity of every selectable backend capability.

Single source of truth for provider ids across the whole system:

- the bot uses it for the chain-start switcher commands, the user-config
  default and /reset fan-out;
- the worker runtime uses it to advance a failed chat request along the
  failover chain;
- CDK worker provisioning can iterate it later.

This module is pure stdlib on purpose: ``scripts/build_bundles.py`` copies it
into both Lambda bundles (``lambda/`` and ``engines/``) and the repo root
imports it for tests and CDK synth.

A provider is an external API (Gemini, Ollama Cloud, DeepL, Ideogram) plus its
bot-side worker Lambda. Kinds:

- ``chat`` — keeps per-user conversation context and participates in the
  failover chain;
- ``translate`` — single provider, stateless (DeepL);
- ``image`` — single provider, produces photos (Ideogram).

Chat requests start at the user's conversation-start provider (user-config
``engines`` field, legacy name) and, when that provider fails, advance along
``CHAT_CHAIN_ORDER``; the tail provider replies a user-facing error. See
``docs/adr/0001`` (why the word "engine" survives on wire/storage names) and
``CONTEXT.md`` for the domain vocabulary.
"""

from dataclasses import dataclass

# Chat providers try the next one in this order when the current one fails.
CHAT_CHAIN_ORDER: tuple[str, ...] = ("gemini", "qwen", "llama")

DEFAULT_CHAT_PROVIDER = CHAT_CHAIN_ORDER[0]

# The content flavor a chat provider's answer carries when the responder does
# not declare one. It is a contract *between* the two bundles — the engines
# declare it on the wire and the Telegram sender picks a renderer from it — so
# it lives here, in the module build_bundles.py copies into both. A literal
# duplicated on either side silently pins that side to a stale renderer.
DEFAULT_CONTENT_FLAVOR = "llm"


@dataclass(frozen=True)
class Provider:
    """One entry in the catalog."""

    kind: str  # "chat" | "translate" | "image"
    label: str  # human name shown to the user


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(kind="chat", label="Gemini"),
    "qwen": Provider(kind="chat", label="Qwen 3.5"),
    "llama": Provider(kind="chat", label="Llama 4"),
    "deepl": Provider(kind="translate", label="DeepL"),
    "ideogram": Provider(kind="image", label="Ideogram"),
}


def chat_provider_ids() -> tuple[str, ...]:
    """Every provider id that participates in chat failover."""
    return CHAT_CHAIN_ORDER


def is_chat_provider(provider_id: str) -> bool:
    return provider_id in CHAT_CHAIN_ORDER


def chain_next(provider_id: str) -> str | None:
    """The provider that handles a chat request when ``provider_id`` fails."""
    try:
        position = CHAT_CHAIN_ORDER.index(provider_id)
    except ValueError:
        return None
    if position + 1 < len(CHAT_CHAIN_ORDER):
        return CHAT_CHAIN_ORDER[position + 1]
    return None  # tail of the chain: no further provider


def provider_label(provider_id: str) -> str:
    provider = PROVIDERS.get(provider_id)
    return provider.label if provider else provider_id
