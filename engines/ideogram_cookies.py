"""Ideogram session-cookie normalization.

``ig-cookies.json`` in the bot bucket has two legitimate shapes:

- a **mapping** ``{name: value}`` — what the engine itself writes
  (``dict(response.cookies)`` in :mod:`engines.ideogram_img`);
- a **browser export**: a list of cookie objects with ``name``/``value`` keys
  (plus ``domain``, ``path``, ``expirationDate``, ...) — what a human produces
  when seeding the file from DevTools.

The engine only needs the ``session_cookie`` value; both shapes must resolve to
it, and anything unparseable must degrade to "no cookie" (which triggers a
refresh) instead of crashing the engine with a TypeError.
"""

from typing import Any

SESSION_COOKIE = "session_cookie"


def cookie_mapping(raw: Any) -> dict[str, str]:
    """Normalize either supported cookie shape into ``{name: value}``."""
    if isinstance(raw, dict):
        return {
            str(name): str(value)
            for name, value in raw.items()
            if isinstance(value, (str, int, float))
        }
    if isinstance(raw, list):
        mapping = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if name is None or not isinstance(value, (str, int, float)):
                continue
            mapping[str(name)] = str(value)
        return mapping
    return {}


def session_cookie(raw: Any) -> str | None:
    """The session cookie value from either supported shape, or ``None``."""
    return cookie_mapping(raw).get(SESSION_COOKIE)


def cookie_header(raw: Any) -> str:
    """The ``Cookie`` header value the Ideogram API expects (session cookie only)."""
    value = session_cookie(raw)
    return f"{SESSION_COOKIE}={value}" if value else ""
