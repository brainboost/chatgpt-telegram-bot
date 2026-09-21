"""Gemini web credentials and bootstrap tokens.

The engine authenticates as the *web app*, not as an API client: it replays the
``__Secure-1PSID`` session cookies of a logged-in browser against
``gemini.google.com``. That is what puts the bot on the same free-tier quota as
the Gemini web chat (compute-based limits that refresh every five hours) instead
of the API key's own limits.

Two kinds of secret travel together, and they have different lifetimes:

- **cookies** — long-lived, operator-provided. They are stored in the bot bucket
  as ``gemini-cookies.json``. The accepted shapes are a DevTools/browser export
  (a list of cookie objects), a plain ``{name: value}`` mapping, or a nested
  ``{"cookies": ..., "access_token": ...}`` object. Only ``__Secure-1PSID`` and
  ``__Secure-1PSIDTS`` are actually sent: replaying the other Google cookies is
  documented to make Google answer 401 while it rotates the session.
- **the ``at`` token** (page global ``SNlM0e``) — an anti-CSRF token bound to the
  session. Google stopped embedding it in the ``/app`` HTML around April 2026, so
  it is scraped when present and otherwise read from the cache in
  ``gemini-token.json``, which the engine keeps up to date whenever a scrape does
  succeed. It is required: a request without it is rejected with HTTP 400.

The two files are deliberately separate. ``gemini-cookies.json`` is operator
input and is never rewritten; ``gemini-token.json`` is a machine-managed cache,
so refreshing the token can never damage the exported session.

The browser identity matters as much as the cookies. Chrome 146 on Windows turns
on Device Bound Session Credentials, which binds a session to a device key that a
replaying client cannot produce, so the session is pinned to ``chrome145``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from botocore.exceptions import BotoCoreError, ClientError
from curl_cffi import requests

from .common_utils import read_json_from_s3, read_ssm_param, save_to_s3

logger = logging.getLogger(__name__)

GEMINI_COOKIES_FILE = "gemini-cookies.json"
GEMINI_TOKEN_FILE = "gemini-token.json"
APP_URL = "https://gemini.google.com/app"

# The only cookies the web app's own client replays.
AUTH_COOKIE_NAMES: tuple[str, ...] = ("__Secure-1PSID", "__Secure-1PSIDTS")

# chrome146+ enables Device Bound Session Credentials, which cookie replay
# cannot satisfy; chrome145 is the newest profile that still works.
IMPERSONATE = "chrome145"

DEFAULT_LANGUAGE = "en"
_REQUEST_TIMEOUT = 60

# Page globals on https://gemini.google.com/app. All are optional; the regexes
# are deliberately substring searches over the whole HTML, matching what the
# maintained browser client does.
_ACCESS_TOKEN_RE = re.compile(r'"SNlM0e":\s*"(.*?)"')
_BUILD_LABEL_RE = re.compile(r'"cfb2h":\s*"(.*?)"')
_SESSION_ID_RE = re.compile(r'"FdrFJe":\s*"(.*?)"')
_LANGUAGE_RE = re.compile(r'"TuX5cc":\s*"(.*?)"')
_TOKEN_KEYS = ("cookies", "access_token")


class CredentialsError(RuntimeError):
    """Stored credentials are missing or unusable; an operator must refresh them."""


@dataclass(frozen=True)
class GeminiCredentials:
    """The cookies and cached ``at`` token replayed to the web backend."""

    cookies: dict[str, str] = field(default_factory=dict)
    access_token: str | None = None

    def has_session(self) -> bool:
        return bool(self.cookies.get(AUTH_COOKIE_NAMES[0]))


@dataclass(frozen=True)
class BootstrapTokens:
    """Per-page-load values scraped from the app HTML."""

    access_token: str | None = None
    build_label: str | None = None
    session_id: str | None = None
    language: str = DEFAULT_LANGUAGE


def _mapping_from_cookies(raw: object) -> dict[str, str]:
    """Normalize a browser export or a ``{name: value}`` mapping."""
    if isinstance(raw, dict):
        source = raw.items()
    elif isinstance(raw, list):
        source = (
            (item.get("name"), item.get("value"))
            for item in raw
            if isinstance(item, dict)
        )
    else:
        return {}
    return {
        str(name): str(value)
        for name, value in source
        if isinstance(name, str)
        and name
        and isinstance(value, (str, int, float))
        and not isinstance(value, bool)
    }


def load_credentials(raw: object) -> GeminiCredentials:
    """Read either supported credential shape into :class:`GeminiCredentials`.

    Raises :class:`CredentialsError` when no usable session cookie is present, so
    a misconfigured bucket fails loudly instead of silently answering as a guest.
    """
    cookies: dict[str, str] = {}
    access_token: str | None = None

    if isinstance(raw, dict) and any(key in raw for key in _TOKEN_KEYS):
        cookies = _mapping_from_cookies(raw.get("cookies"))
        cached = raw.get("access_token")
        access_token = str(cached) if isinstance(cached, str) and cached else None
    else:
        cookies = _mapping_from_cookies(raw)

    credentials = GeminiCredentials(
        cookies={
            name: value
            for name, value in cookies.items()
            if name in AUTH_COOKIE_NAMES
        },
        access_token=access_token,
    )
    if not credentials.has_session():
        raise CredentialsError(
            f"No '{AUTH_COOKIE_NAMES[0]}' cookie in {GEMINI_COOKIES_FILE}. "
            "Export the Google cookies from a logged-in browser session and "
            "store them in the bot bucket."
        )
    return credentials


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def scrape_tokens(html: str) -> BootstrapTokens:
    """Extract the page globals from the app HTML, tolerating missing ones."""
    locale = _first(_LANGUAGE_RE, html)
    return BootstrapTokens(
        access_token=_first(_ACCESS_TOKEN_RE, html),
        build_label=_first(_BUILD_LABEL_RE, html),
        session_id=_first(_SESSION_ID_RE, html),
        # The page reports a locale such as "en-US"; the wire protocol wants the
        # bare language tag ("en") in both `hl` and payload slot 1.
        language=(locale.split("-")[0].lower() if locale else DEFAULT_LANGUAGE)
        or DEFAULT_LANGUAGE,
    )


def new_session(credentials: GeminiCredentials) -> requests.Session:
    """A browser-impersonating session carrying only the two auth cookies."""
    session = requests.Session(impersonate=IMPERSONATE)
    for name, value in credentials.cookies.items():
        session.cookies.set(name, value, domain=".google.com")
    return session


def load_stored_credentials(
    *,
    reader: Callable[..., object] = read_json_from_s3,
    param_reader: Callable[..., str] = read_ssm_param,
) -> GeminiCredentials:
    """Read and validate the stored cookies, plus the cached ``at`` token.

    ``reader`` and ``param_reader`` are the injectable I/O seams (tests pass
    plain callables, so no AWS client is constructed).
    """
    bucket = param_reader(param_name="BOT_S3_BUCKET")
    raw = reader(bucket_name=bucket, file_name=GEMINI_COOKIES_FILE)
    if raw is None:
        raise CredentialsError(
            f"Cannot read '{GEMINI_COOKIES_FILE}' from bucket '{bucket}'."
        )
    credentials = load_credentials(raw)
    if credentials.access_token:
        return credentials
    cached = reader(bucket_name=bucket, file_name=GEMINI_TOKEN_FILE)
    token = cached.get("access_token") if isinstance(cached, dict) else None
    if isinstance(token, str) and token:
        logger.info("Loaded a cached Gemini access token (len=%d)", len(token))
        return replace(credentials, access_token=token)
    return credentials


def save_access_token(
    token: str,
    *,
    saver: Callable[..., None] = save_to_s3,
    param_reader: Callable[..., str] = read_ssm_param,
) -> bool:
    """Cache a freshly scraped ``at`` token for the next cold start.

    Best effort: the token is a cache, never a turn blocker, so a bucket write
    failure is logged and swallowed.
    """
    if not token:
        return False
    try:
        bucket = param_reader(param_name="BOT_S3_BUCKET")
        saver(
            bucket_name=bucket,
            file_name=GEMINI_TOKEN_FILE,
            value={"access_token": token},
        )
    except (BotoCoreError, ClientError, OSError) as e:
        logger.warning("Could not cache the Gemini access token", exc_info=e)
        return False
    logger.info("Cached a refreshed Gemini access token (len=%d)", len(token))
    return True


def fetch_tokens(
    session: requests.Session,
    *,
    url: str = APP_URL,
    timeout: int = _REQUEST_TIMEOUT,
) -> BootstrapTokens:
    """Load the app page and scrape its bootstrap globals.

    A non-200 answer is fatal: the usual cause is a 429 from Google flagging the
    caller's IP, which no amount of retrying within the invocation will fix.
    """
    response = session.get(
        url,
        headers={
            "accept-language": f"{DEFAULT_LANGUAGE}-US,{DEFAULT_LANGUAGE};q=0.9",
            "content-type": "application/x-www-form-urlencoded;charset=utf-8",
            "origin": "https://gemini.google.com",
            "referer": "https://gemini.google.com/",
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise CredentialsError(
            f"Gemini app page returned HTTP {response.status_code}. "
            "A 429 means Google has temporarily flagged this IP; otherwise the "
            "stored cookies are likely expired."
        )
    return scrape_tokens(response.text)
