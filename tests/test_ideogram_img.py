"""Offline tests for the Ideogram image engine (prod '/imagine' TypeError).

``ig-cookies.json`` reaches the engine in two legitimate shapes: the mapping the
engine writes itself (``{name: value}``) and the browser export a human seeds
(a list of cookie objects with ``name``/``value``). The prod failure was a
browser-shaped file hitting ``cookies["session_cookie"]`` — a list indexed as a
mapping — so these tests drive ``request_images`` through both shapes with AWS
and the network stubbed out.
"""

import importlib
import json
import types

import jwt
import pytest

img = importlib.import_module("engines.ideogram_img")
cookies_mod = importlib.import_module("engines.ideogram_cookies")

ACCESS_TOKEN = "access-token"
REQUEST_ID = "req-123"
SIGNING_KEY = "test-signing-key-that-is-long-enough-for-hs256"


def _jwt(offset_seconds: int = 3600, **claims) -> str:
    exp = int(__import__("time").time()) + offset_seconds
    return jwt.encode({"exp": exp, **claims}, SIGNING_KEY)


def _browser_export(session_cookie_value: str) -> list[dict]:
    """The shape a DevTools export produces (see the user's ig-cookies.json)."""
    return [
        {
            "domain": ".ideogram.ai",
            "expirationDate": 1900000000,
            "hostOnly": False,
            "httpOnly": True,
            "name": "__cf_bm",
            "path": "/",
            "sameSite": "no_restriction",
            "secure": True,
            "session": False,
            "storeId": "0",
            "value": "unrelated-cookie",
            "id": 1,
        },
        {
            "domain": ".ideogram.ai",
            "expirationDate": 1900000000,
            "hostOnly": False,
            "httpOnly": True,
            "name": "session_cookie",
            "path": "/",
            "sameSite": "no_restriction",
            "secure": True,
            "session": False,
            "storeId": "0",
            "value": session_cookie_value,
            "id": 2,
        },
    ]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture
def engine(monkeypatch):
    """The engine with AWS, queue lookup and token refresh stubbed out."""
    monkeypatch.setattr(img, "_bucket_name", "test-bucket")
    monkeypatch.setattr(img, "_ideogram_user", "test-user")
    monkeypatch.setattr(img, "headers", dict(img.headers))
    monkeypatch.setattr(
        img, "check_and_refresh_auth_tokens", lambda: {"access_token": ACCESS_TOKEN}
    )
    return img


def _capture_post(monkeypatch, sent: dict) -> None:
    def post(url, headers, data, impersonate=None):
        sent["url"] = url
        sent["headers"] = headers
        sent["data"] = data
        return _FakeResponse({"request_id": REQUEST_ID})

    monkeypatch.setattr(img, "requests", types.SimpleNamespace(post=post))


def test_request_images_accepts_browser_export_cookies(engine, monkeypatch):
    """Prod regression: a list-shaped ig-cookies.json must not crash."""
    session = _jwt()
    monkeypatch.setattr(
        img, "read_json_from_s3", lambda bucket_name, file_name: _browser_export(session)
    )
    sent: dict = {}
    _capture_post(monkeypatch, sent)

    assert engine.request_images(prompt="a cat") == REQUEST_ID
    assert sent["headers"]["Cookie"] == f"session_cookie={session}"
    assert sent["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


def test_request_images_accepts_mapping_cookies(engine, monkeypatch):
    """The shape the engine writes itself keeps working."""
    session = _jwt()
    monkeypatch.setattr(
        img,
        "read_json_from_s3",
        lambda bucket_name, file_name: {"session_cookie": session},
    )
    sent: dict = {}
    _capture_post(monkeypatch, sent)

    assert engine.request_images(prompt="a cat") == REQUEST_ID
    assert sent["headers"]["Cookie"] == f"session_cookie={session}"


def test_request_images_refreshes_when_no_usable_session_cookie(engine, monkeypatch):
    """A file without a session cookie falls back to the login flow."""
    refreshed = {"session_cookie": _jwt()}
    monkeypatch.setattr(
        img, "read_json_from_s3", lambda bucket_name, file_name: [{"name": "__cf_bm", "value": "x"}]
    )
    monkeypatch.setattr(img, "get_session_cookies", lambda iss_token: refreshed)
    sent: dict = {}
    _capture_post(monkeypatch, sent)

    assert engine.request_images(prompt="a cat") == REQUEST_ID
    assert sent["headers"]["Cookie"] == f"session_cookie={refreshed['session_cookie']}"


def test_request_images_refreshes_when_cookie_file_is_missing(engine, monkeypatch):
    def boom(bucket_name, file_name):
        raise FileNotFoundError(file_name)

    monkeypatch.setattr(img, "read_json_from_s3", boom)
    refreshed = {"session_cookie": _jwt()}
    monkeypatch.setattr(img, "get_session_cookies", lambda iss_token: refreshed)
    sent: dict = {}
    _capture_post(monkeypatch, sent)

    assert engine.request_images(prompt="a cat") == REQUEST_ID
    assert sent["headers"]["Cookie"] == f"session_cookie={refreshed['session_cookie']}"


def test_request_images_posts_the_captured_payload_shape(engine, monkeypatch):
    """The engine sends the payload shape ideogram.ai itself uses today."""
    monkeypatch.setattr(
        img, "read_json_from_s3", lambda bucket_name, file_name: _browser_export(_jwt())
    )
    sent: dict = {}
    _capture_post(monkeypatch, sent)

    assert engine.request_images(prompt="a cat") == REQUEST_ID
    body = json.loads(sent["data"])
    assert body["prompt"] == "a cat"
    assert body["user_id"] == "test-user"
    assert body["model_version"] == "AUTO"
    assert body["model_uri"] == "model/AUTO/version/0"
    assert body["use_autoprompt_option"] == "AUTO"
    assert body["sampling_speed"] == 2
    assert body["style_type"] == "AUTO"
    assert body["num_images"] == 4
    assert body["resolution"] == {"width": 1024, "height": 1024}
    # fields from the old hand-built payload are gone
    assert "style_expert" not in body
    assert "aspect_ratio" not in body


def test_request_images_raises_on_api_error(engine, monkeypatch):
    monkeypatch.setattr(
        img, "read_json_from_s3", lambda bucket_name, file_name: _browser_export(_jwt())
    )

    class _Failed(_FakeResponse):
        def __init__(self):
            super().__init__({})
            self.ok = False
            self.text = "nope"

    monkeypatch.setattr(
        img, "requests", types.SimpleNamespace(post=lambda **kwargs: _Failed())
    )
    with pytest.raises(Exception, match="Error response"):
        engine.request_images(prompt="a cat")


def test_cookie_normalization_handles_junk():
    assert cookies_mod.cookie_mapping(None) == {}
    assert cookies_mod.cookie_mapping("nonsense") == {}
    assert cookies_mod.cookie_mapping([{"name": "a"}, "b", 3]) == {}
    assert cookies_mod.session_cookie([]) is None
    assert cookies_mod.cookie_header([]) == ""
