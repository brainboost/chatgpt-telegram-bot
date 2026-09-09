"""Tests for the provider catalog (candidate 5 deepening).

The catalog is the single source of provider identity: chain order, kinds,
defaults and the failover ``chain_next`` helper. Pure data — no AWS anywhere.
"""

import providers


def test_chat_chain_starts_at_gemini():
    assert providers.DEFAULT_CHAT_PROVIDER == "gemini"
    assert providers.chat_provider_ids() == ("gemini", "qwen", "llama")


def test_chain_next_advances_along_the_chain():
    assert providers.chain_next("gemini") == "qwen"
    assert providers.chain_next("qwen") == "llama"


def test_chain_next_returns_none_at_the_tail():
    assert providers.chain_next("llama") is None


def test_chain_next_returns_none_for_unknown_and_non_chat():
    assert providers.chain_next("bogus") is None
    assert providers.chain_next("deepl") is None


def test_every_chat_provider_is_registered():
    for provider_id in providers.chat_provider_ids():
        assert provider_id in providers.PROVIDERS
        assert providers.PROVIDERS[provider_id].kind == "chat"
        assert providers.is_chat_provider(provider_id)


def test_is_chat_provider_excludes_translate_and_image():
    assert not providers.is_chat_provider("deepl")
    assert not providers.is_chat_provider("ideogram")
    assert not providers.is_chat_provider("bogus")


def test_catalog_holds_the_full_provider_set():
    assert set(providers.PROVIDERS) == {"gemini", "qwen", "llama", "deepl", "ideogram"}
    assert providers.PROVIDERS["deepl"].kind == "translate"
    assert providers.PROVIDERS["ideogram"].kind == "image"


def test_provider_label_falls_back_to_the_id():
    assert providers.provider_label("gemini") == "Gemini"
    assert providers.provider_label("bogus") == "bogus"
