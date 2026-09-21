"""Tests for the Gemini web credentials and bootstrap-token scrape.

All I/O is injected, so these run offline: no bucket, no network, no Google.
"""

import json

import pytest
from botocore.exceptions import ClientError

from engines.gemini_auth import (
    AUTH_COOKIE_NAMES,
    BootstrapTokens,
    CredentialsError,
    GeminiCredentials,
    fetch_tokens,
    load_credentials,
    load_stored_credentials,
    save_access_token,
    scrape_tokens,
)

BROWSER_EXPORT = [
    {"name": "__Secure-1PSID", "value": "psid-value", "domain": ".google.com"},
    {"name": "SID", "value": "not-used", "domain": ".google.com"},
    {"name": "__Secure-1PSIDTS", "value": "psidts-value", "domain": ".google.com"},
    {"name": "NID", "value": "not-used", "domain": ".google.com"},
]


def test_browser_export_keeps_only_the_auth_cookies():
    credentials = load_credentials(BROWSER_EXPORT)

    assert credentials.cookies == {
        "__Secure-1PSID": "psid-value",
        "__Secure-1PSIDTS": "psidts-value",
    }
    assert credentials.access_token is None
    assert credentials.has_session()


def test_plain_name_value_mapping_is_accepted():
    credentials = load_credentials(
        {"__Secure-1PSID": "psid-value", "SID": "not-used"}
    )

    assert credentials.cookies == {"__Secure-1PSID": "psid-value"}


def test_nested_shape_carries_the_cached_access_token():
    credentials = load_credentials(
        {"cookies": BROWSER_EXPORT, "access_token": "cached-token"}
    )

    assert credentials.access_token == "cached-token"
    assert credentials.cookies == {
        "__Secure-1PSID": "psid-value",
        "__Secure-1PSIDTS": "psidts-value",
    }


def test_missing_session_cookie_fails_loudly():
    with pytest.raises(CredentialsError) as error:
        load_credentials([{"name": "SID", "value": "x"}])

    assert AUTH_COOKIE_NAMES[0] in str(error.value)


@pytest.mark.parametrize("raw", [None, "a string", 42, [], {}, [None, "x"]])
def test_unusable_shapes_fail_loudly(raw):
    with pytest.raises(CredentialsError):
        load_credentials(raw)


def test_non_string_cookie_values_are_dropped_not_stringified_oddly():
    credentials = load_credentials([{"name": "__Secure-1PSID", "value": 12345}])
    assert credentials.cookies == {"__Secure-1PSID": "12345"}


def test_scrape_reads_every_known_page_global():
    html = (
        '<script>{"cfb2h":"build_1","FdrFJe":"-866040998427962646",'
        '"TuX5cc":"pl","qKIAYe":"push-1","SNlM0e":"token:123"}</script>'
    )

    tokens = scrape_tokens(html)

    assert tokens == BootstrapTokens(
        access_token="token:123",
        build_label="build_1",
        session_id="-866040998427962646",
        language="pl",
    )


def test_scrape_tolerates_the_missing_access_token():
    """Google stopped embedding SNlM0e in /app HTML; the token is then cached."""
    html = '<script>{"cfb2h":"build_1","FdrFJe":"123"}</script>'

    tokens = scrape_tokens(html)

    assert tokens.access_token is None
    assert tokens.build_label == "build_1"
    assert tokens.session_id == "123"
    assert tokens.language == "en"  # documented default when TuX5cc is absent


def test_scrape_of_an_empty_page_yields_defaults():
    tokens = scrape_tokens("<html></html>")

    assert tokens.access_token is None
    assert tokens.build_label is None
    assert tokens.session_id is None
    assert tokens.language == "en"


def test_scrape_normalizes_a_regional_locale_to_a_language_tag():
    """`hl` and payload slot 1 take "en", but the page reports "en-US"."""
    assert scrape_tokens('"TuX5cc":"en-US"').language == "en"
    assert scrape_tokens('"TuX5cc":"pt-BR"').language == "pt"


def test_stored_credentials_are_read_through_the_injected_seams():
    reads = []

    def reader(*, bucket_name, file_name):
        reads.append((bucket_name, file_name))
        return BROWSER_EXPORT

    credentials = load_stored_credentials(
        reader=reader, param_reader=lambda *, param_name: "the-bucket"
    )

    assert reads == [
        ("the-bucket", "gemini-cookies.json"),
        ("the-bucket", "gemini-token.json"),
    ]
    assert credentials.cookies["__Secure-1PSID"] == "psid-value"


def _two_file_reader(cookies_raw, token_raw):
    def reader(*, bucket_name, file_name):
        if file_name == "gemini-cookies.json":
            return cookies_raw
        return token_raw

    return reader


def test_the_token_file_supplies_the_cached_access_token():
    credentials = load_stored_credentials(
        reader=_two_file_reader(BROWSER_EXPORT, {"access_token": "cached:1"}),
        param_reader=lambda **_: "b",
    )

    assert credentials.access_token == "cached:1"


def test_the_token_file_is_optional():
    credentials = load_stored_credentials(
        reader=_two_file_reader(BROWSER_EXPORT, None), param_reader=lambda **_: "b"
    )

    assert credentials.access_token is None


@pytest.mark.parametrize("cached", ["", "   ", None, [], {"access_token": ""}, 42])
def test_an_empty_or_junk_token_cache_is_ignored(cached):
    """The cache is optional, so a placeholder object must not break the engine."""
    credentials = load_stored_credentials(
        reader=_two_file_reader(BROWSER_EXPORT, cached), param_reader=lambda **_: "b"
    )

    assert credentials.access_token is None
    assert credentials.has_session()


def test_a_truncated_token_cache_is_ignored_not_fatal():
    """Seeding is done by hand, so a half-written object is a real possibility."""

    def reader(*, bucket_name, file_name):
        if file_name == "gemini-cookies.json":
            return BROWSER_EXPORT
        raise json.JSONDecodeError("Expecting value", "", 0)

    credentials = load_stored_credentials(reader=reader, param_reader=lambda **_: "b")

    assert credentials.access_token is None
    assert credentials.has_session()


def test_a_token_embedded_in_the_cookies_file_short_circuits_the_cache_read():
    reads = []

    def reader(*, bucket_name, file_name):
        reads.append(file_name)
        return {"cookies": BROWSER_EXPORT, "access_token": "inline:1"}

    credentials = load_stored_credentials(reader=reader, param_reader=lambda **_: "b")

    assert credentials.access_token == "inline:1"
    assert reads == ["gemini-cookies.json"]


def test_a_missing_bucket_object_names_the_file():
    with pytest.raises(CredentialsError) as error:
        load_stored_credentials(
            reader=lambda **_: None, param_reader=lambda **_: "the-bucket"
        )

    assert "gemini-cookies.json" in str(error.value)


def test_save_access_token_writes_the_cache_object():
    written = {}

    saved = save_access_token(
        "tok:1",
        saver=lambda **kwargs: written.update(kwargs),
        param_reader=lambda **_: "the-bucket",
    )

    assert saved is True
    assert written == {
        "bucket_name": "the-bucket",
        "file_name": "gemini-token.json",
        "value": {"access_token": "tok:1"},
    }


def test_save_access_token_ignores_an_empty_token():
    assert save_access_token("", saver=lambda **_: pytest.fail("wrote a token")) is False


def test_save_access_token_swallows_a_bucket_failure():
    def denied(**_):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject"
        )

    assert (
        save_access_token("tok:1", saver=denied, param_reader=lambda **_: "b") is False
    )


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.request = None

    def get(self, url, *, headers, timeout):
        self.request = {"url": url, "headers": headers, "timeout": timeout}
        return self.response


def test_fetch_tokens_posts_the_app_page_headers():
    session = FakeSession(FakeResponse(text='"cfb2h":"b","FdrFJe":"s"'))

    tokens = fetch_tokens(session)

    assert session.request["url"] == "https://gemini.google.com/app"
    assert session.request["headers"]["referer"] == "https://gemini.google.com/"
    assert tokens.build_label == "b"


def test_a_throttled_app_page_reports_the_ip_problem():
    session = FakeSession(FakeResponse(status_code=429))

    with pytest.raises(CredentialsError) as error:
        fetch_tokens(session)

    assert "429" in str(error.value)


def test_credentials_default_to_no_cookies_but_report_no_session():
    assert GeminiCredentials().has_session() is False
