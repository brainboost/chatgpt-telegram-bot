"""The Gemini web chat wire protocol.

Everything in this module is a transcription of what ``gemini.google.com``
actually exchanges, reverse-engineered from a captured HAR and then replayed
live. See ``docs/gemini-web-protocol.md`` for the evidence and for how to
recapture it when Google changes the format.

Shape of one turn:

1. ``GET /app`` yields the page globals (``at`` token, ``bl`` build label,
   ``f.sid`` session id) — see :mod:`engines.gemini_auth`.
2. ``POST .../StreamGenerate?bl=..&f.sid=..&hl=..&_reqid=..&rt=c`` with a form body
   ``f.req=<json>[&at=<token>]``. ``f.req`` is a two-element array whose second
   element is a JSON *string* holding an 81-slot payload list.
3. The response is chunked: ``)]}'``, then repeated ``<length>`` / ``<json>`` line
   pairs. Each JSON value is an array of frames; a result frame is
   ``["wrb.fr", rpcid, "<inner json string>", ...]``.

Conversation history lives on Google's side, so a follow-up turn is identical to
a first turn except for payload slot 2, which carries ``[cid, rid, rcid, ...,
context]`` from the previous answer. That is the only mechanism this API offers:
payload slot 0 holds a single message, so local history replay is not possible.
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from .gemini_auth import (
    BootstrapTokens,
    GeminiCredentials,
    fetch_tokens,
    load_stored_credentials,
    new_session,
    save_access_token,
)

logger = logging.getLogger(__name__)

STREAM_URL = (
    "https://gemini.google.com/_/BardChatUi/data/"
    "assistant.lamda.BardFrontendService/StreamGenerate"
)

INNER_SLOTS = 81
METADATA_SLOTS = 10
CONTEXT_SLOT = 9

MODEL_HEADER_KEY = "x-goog-ext-525001261-jspb"
REQUEST_UUID_HEADER = "x-goog-ext-525005358-jspb"
STATIC_HEADERS = {
    "x-goog-ext-73010989-jspb": "[0]",
    "x-goog-ext-73010990-jspb": "[0,0,0]",
}

# Free-tier Flash at capture time. These hashes are account- and
# deployment-dependent and Google rotates them; a stale id surfaces as error
# 1050/1052, which fails the request over to the next provider.
DEFAULT_MODEL_ID = "fbb127bbb056c959"
DEFAULT_MODEL_NUMBER = 1  # 1 = Flash, 3 = Pro, 6 = Lite
FREE_CAPACITY = 1
CAPABILITIES = [4, 5, 6, 8]

DEFAULT_LANGUAGE = "en"
_REQID_START = 100_000
_REQID_STEP = 100_000
_REQUEST_TIMEOUT = 300

# In-stream error codes arrive in frame[5] on an HTTP 200 response.
_USAGE_LIMIT = 1037
_IP_BLOCKED = 1060
_REQUEST_REJECTED = 7

_FOLLOWUP_RE = re.compile(r"\s*<FollowUp\b[^>]*/?>(?:\s*</FollowUp>)?", re.IGNORECASE)
_ARTIFACT_RE = re.compile(r"https?://googleusercontent\.com/(?:\w+/)*\d+\n*")


class GeminiError(RuntimeError):
    """The web backend answered, but the turn did not complete."""


class UsageLimitError(GeminiError):
    """Free-tier compute quota for the current five-hour window is spent."""


class TemporarilyBlockedError(GeminiError):
    """Google has temporarily flagged this IP (HTTP 429 or in-stream 1060)."""


class RequestRejectedError(GeminiError):
    """The request was refused — usually dead cookies or an invalid payload."""


class StreamAbortedError(GeminiError):
    """The stream ended without any answer text."""


@dataclass
class ConversationState:
    """The identifiers that make the next turn a follow-up.

    ``cid``/``rid``/``rcid`` identify the thread, the last answer and the last
    answer candidate; ``context`` is the continuation token Google returns once
    the turn has been committed to history.
    """

    cid: str = ""
    rid: str = ""
    rcid: str = ""
    context: str = ""

    def to_metadata(self) -> list:
        """Payload slot 2. An empty state yields the "new conversation" default."""
        metadata: list = [self.cid, self.rid, self.rcid]
        metadata.extend([None] * (CONTEXT_SLOT - len(metadata)))
        metadata.append(self.context)
        return metadata

    @classmethod
    def from_metadata(cls, value: Any) -> ConversationState:
        if not isinstance(value, list):
            return cls()
        parts = list(value) + [None] * (METADATA_SLOTS - len(value))

        def text(index: int) -> str:
            item = parts[index]
            return item if isinstance(item, str) else ""

        return cls(
            cid=text(0), rid=text(1), rcid=text(2), context=text(CONTEXT_SLOT)
        )

    def is_new(self) -> bool:
        return not self.cid

    def to_dict(self) -> dict:
        """The persistable form (an empty state is stored as ``{}``)."""
        if self.is_new():
            return {}
        return {
            "cid": self.cid,
            "rid": self.rid,
            "rcid": self.rcid,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ConversationState:
        if not isinstance(value, dict):
            return cls()
        return cls(
            cid=str(value.get("cid") or ""),
            rid=str(value.get("rid") or ""),
            rcid=str(value.get("rcid") or ""),
            context=str(value.get("context") or ""),
        )


@dataclass(frozen=True)
class TurnResult:
    """One completed turn: the answer text plus the state for the next turn."""

    text: str
    state: ConversationState = field(default_factory=ConversationState)


@dataclass(frozen=True)
class GenerateRequest:
    """Everything needed to perform one StreamGenerate POST."""

    url: str
    params: dict[str, str]
    headers: dict[str, str]
    data: dict[str, str]


def strip_annotations(text: str) -> str:
    """Remove web-app decorations that are not part of the answer.

    Gemini web appends a ``<FollowUp label="…" query="…"/>`` element to suggest
    the next question, and occasionally emits ``googleusercontent`` artifact
    URLs. Neither belongs in a Telegram message, and stripping must happen on
    the accumulated text because the tags can be split across stream deltas.
    """
    return _ARTIFACT_RE.sub("", _FOLLOWUP_RE.sub("", text)).strip()


def build_model_header(
    model_id: str,
    *,
    capacity: int = FREE_CAPACITY,
    model_number: int = DEFAULT_MODEL_NUMBER,
    extended_thinking: bool = True,
    session_uuid: str = "",
) -> str:
    """The ``x-goog-ext-525001261-jspb`` value selecting model and tier."""
    header: list = [
        1, None, None, None, model_id, None, None, 0, list(CAPABILITIES),
        None, None, capacity, None, None, model_number,
        2 if extended_thinking else 1,
        session_uuid,
    ]
    return json.dumps(header, separators=(",", ":"))


def build_payload(
    text: str,
    state: ConversationState,
    *,
    language: str = DEFAULT_LANGUAGE,
    model_number: int = DEFAULT_MODEL_NUMBER,
    extended_thinking: bool = True,
    request_uuid: str = "",
) -> str:
    """Build the ``f.req`` value for one turn.

    Unset slots stay ``None`` — notably slot 3, which is the Deep Research token
    and must not be populated for an ordinary chat.
    """
    inner: list = [None] * INNER_SLOTS
    inner[0] = [text, 0, None, None, None, None, 0]
    inner[1] = [language]
    inner[2] = state.to_metadata()
    inner[6] = [1]
    inner[7] = 1  # streaming
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[0]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [1]
    inner[53] = 0
    inner[59] = request_uuid
    inner[61] = []
    inner[68] = 1
    inner[79] = model_number
    inner[80] = 2 if extended_thinking else 1
    return json.dumps([None, json.dumps(inner)], separators=(",", ":"))


def build_generate_request(
    text: str,
    state: ConversationState,
    *,
    tokens: BootstrapTokens,
    access_token: str | None,
    model_id: str = DEFAULT_MODEL_ID,
    model_number: int = DEFAULT_MODEL_NUMBER,
    capacity: int = FREE_CAPACITY,
    extended_thinking: bool = True,
    language: str | None = None,
    reqid: int = _REQID_START,
    session_uuid: str = "",
    request_uuid: str | None = None,
) -> GenerateRequest:
    """Assemble the full POST for one turn."""
    language = language or tokens.language or DEFAULT_LANGUAGE
    request_uuid = request_uuid or str(uuid.uuid4()).upper()
    request_token = access_token or tokens.access_token or ""

    params = {"hl": language, "_reqid": str(reqid), "rt": "c"}
    if tokens.build_label:
        params["bl"] = tokens.build_label
    if tokens.session_id:
        params["f.sid"] = tokens.session_id

    headers = {
        "accept": "*/*",
        "accept-language": f"{language}-US,{language};q=0.9",
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "origin": "https://gemini.google.com",
        "referer": "https://gemini.google.com/",
        "x-same-domain": "1",
        MODEL_HEADER_KEY: build_model_header(
            model_id,
            capacity=capacity,
            model_number=model_number,
            extended_thinking=extended_thinking,
            session_uuid=session_uuid,
        ),
        REQUEST_UUID_HEADER: json.dumps([request_uuid, 1], separators=(",", ":")),
        **STATIC_HEADERS,
    }
    data = {
        "at": request_token,
        "f.req": build_payload(
            text,
            state,
            language=language,
            model_number=model_number,
            extended_thinking=extended_thinking,
            request_uuid=request_uuid,
        ),
    }
    return GenerateRequest(url=STREAM_URL, params=params, headers=headers, data=data)


def iter_frames(raw: str) -> Iterator[list]:
    """Yield frames from a chunked response body.

    The framing is ``)]}'``, then alternating length-marker and JSON lines. The
    marker is deliberately ignored: Google counts it in UTF-16 code units rather
    than bytes, so trusting it desynchronises on any non-BMP answer. JSON never
    contains a raw newline, so a line is always a whole value.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line or line[0] not in "[{":
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, list):
            continue
        for frame in value:
            if isinstance(frame, list):
                yield frame


def _nested(node: Any, path: tuple[int | str, ...]) -> Any:
    """Walk a JSPB response tree, tolerating missing slots and dict keys.

    High-numbered JSPB fields sometimes arrive in a sparse dict in the array's
    last slot instead of positionally, so the walker accepts both.
    """
    for key in path:
        if isinstance(node, list) and isinstance(key, int):
            if len(node) <= key:
                return None
            node = node[key]
        elif isinstance(node, dict) and isinstance(key, str):
            node = node.get(key)
        else:
            return None
    return node


def _frame_error(frame: list) -> int | None:
    """The in-stream error code, if this frame carries one.

    A rejected request puts ``7`` in ``frame[5][0]``; a formatted error puts its
    code at ``frame[5][2][0][1][0]``.
    """
    status = _nested(frame, (5,))
    if not isinstance(status, list) or not status:
        return None
    if status[0] == _REQUEST_REJECTED:
        return _REQUEST_REJECTED
    code = _nested(frame, (5, 2, 0, 1, 0))
    return code if isinstance(code, int) else None


def _raise_for_error(code: int) -> None:
    if code == _USAGE_LIMIT:
        raise UsageLimitError(
            "Gemini free-tier usage limit reached for this five-hour window."
        )
    if code == _IP_BLOCKED:
        raise TemporarilyBlockedError(
            "Google temporarily blocked this IP (error 1060)."
        )
    if code == _REQUEST_REJECTED:
        raise RequestRejectedError(
            "Gemini refused the request; the stored cookies are most likely expired."
        )
    # Unmapped codes do occur (1096 has been seen as a transient throttle after a
    # burst of turns). Log loudly so a persistent one is visible in CloudWatch
    # rather than only showing up as the failover chain quietly taking over.
    logger.error("Gemini returned unmapped in-stream error code %s", code)
    raise GeminiError(f"Gemini returned error code {code}.")


def parse_stream(raw: str) -> TurnResult:
    """Turn a chunked response body into the answer text and next-turn state.

    Candidate text arrives as deltas; the accumulated text is the longest value
    seen for a given candidate marker, so the primary (first) candidate wins and
    the longest text is used only as a fallback when the primary is empty.
    """
    state = ConversationState()
    texts: dict[str, str] = {}
    order: list[str] = []
    completed = False
    error: int | None = None

    for frame in iter_frames(raw):
        code = _frame_error(frame)
        if code is not None and error is None:
            error = code
        if len(frame) < 3 or frame[0] != "wrb.fr" or not frame[2]:
            continue
        try:
            inner = json.loads(frame[2])
        except json.JSONDecodeError:
            logger.warning("Gemini frame carried unparseable inner JSON")
            continue
        if not isinstance(inner, list):
            continue

        identifiers = _nested(inner, (1,))
        if isinstance(identifiers, list):
            cid = _nested(identifiers, (0,))
            rid = _nested(identifiers, (1,))
            if isinstance(cid, str) and cid:
                state.cid = cid
            if isinstance(rid, str) and rid:
                state.rid = rid

        context = _nested(inner, (25,))
        if isinstance(context, str) and context:
            state.context = context
        else:
            # The continuation token is field 25, but JSPB delivers high-numbered
            # fields sparsely — in a dict keyed by *field number + 1* — so on
            # current responses it arrives as metadata["26"] rather than
            # positionally. Continuity works without it (an empty slot 9 is
            # accepted), so this is a fidelity bonus, never a requirement.
            sparse = _nested(inner, (2, "26"))
            if isinstance(sparse, str) and sparse:
                state.context = sparse

        candidate = _nested(inner, (4, 0))
        if isinstance(candidate, list) and candidate:
            marker = candidate[0]
            body = _nested(candidate, (1, 0))
            if isinstance(marker, str) and isinstance(body, str):
                if marker not in texts:
                    order.append(marker)
                if len(body) >= len(texts.get(marker, "")):
                    texts[marker] = body
            if _nested(candidate, (8, 0)) == 2:
                completed = True
            if isinstance(marker, str) and marker:
                state.rcid = marker

    if error is not None:
        _raise_for_error(error)

    answer = ""
    for marker in order:
        if texts.get(marker):
            answer = texts[marker]
            break
    if not answer and texts:
        answer = max(texts.values(), key=len)

    if not answer:
        raise StreamAbortedError(
            "Gemini ended the stream without an answer (the request may have "
            "been silently aborted)."
        )
    if not completed and not state.context:
        logger.warning(
            "Gemini stream ended without a completion marker; returning %d chars",
            len(answer),
        )
    return TurnResult(text=strip_annotations(answer), state=state)


class GeminiWebClient:
    """Lazily initialized client reused across turns in one container.

    Credentials and tokens are resolved once per process: the cookies come from
    the bot bucket, and the ``at`` token is scraped from the app page, falling
    back to the cached copy when Google omits it. A scrape that does succeed is
    written back to the cache, so the stored token self-heals on the runs where
    Google does embed it.
    """

    def __init__(
        self,
        *,
        credentials: GeminiCredentials | None = None,
        session: Any | None = None,
        token_fetcher: Callable[..., BootstrapTokens] = fetch_tokens,
        session_factory: Callable[[GeminiCredentials], Any] = new_session,
        credential_loader: Callable[..., GeminiCredentials] = load_stored_credentials,
        token_saver: Callable[..., bool] = save_access_token,
        model_id: str = DEFAULT_MODEL_ID,
        model_number: int = DEFAULT_MODEL_NUMBER,
        extended_thinking: bool = True,
        timeout: int = _REQUEST_TIMEOUT,
    ) -> None:
        self._credentials = credentials
        self._session = session
        self._token_fetcher = token_fetcher
        self._session_factory = session_factory
        self._credential_loader = credential_loader
        self._token_saver = token_saver
        self._model_id = model_id
        self._model_number = model_number
        self._extended_thinking = extended_thinking
        self._timeout = timeout
        self._tokens: BootstrapTokens | None = None
        self._session_uuid = str(uuid.uuid4()).upper()
        self._reqid = random.randint(10_000, 99_999)

    def _prepare(self) -> tuple[Any, BootstrapTokens]:
        if self._credentials is None:
            self._credentials = self._credential_loader()
        if self._session is None:
            self._session = self._session_factory(self._credentials)
        if self._tokens is None:
            self._tokens = self._token_fetcher(self._session)
            logger.info(
                "Gemini bootstrap: at=%s build=%s sid=%s",
                "present" if self._tokens.access_token else "absent",
                self._tokens.build_label,
                self._tokens.session_id,
            )
            self._cache_scraped_token()
        return self._session, self._tokens

    def _cache_scraped_token(self) -> None:
        """Persist a scraped ``at`` token so the next cold start can reuse it."""
        scraped = self._tokens.access_token if self._tokens else None
        cached = self._credentials.access_token if self._credentials else None
        if not scraped or scraped == cached:
            return
        self._token_saver(scraped)
        if self._credentials is not None:
            self._credentials = replace(self._credentials, access_token=scraped)

    def _access_token(self) -> str | None:
        if self._tokens and self._tokens.access_token:
            return self._tokens.access_token
        cached = self._credentials.access_token if self._credentials else None
        if cached:
            logger.info("Using the cached Gemini access token")
        return cached

    def ask(self, text: str, state: ConversationState | None = None) -> TurnResult:
        """Run one turn, continuing ``state``'s conversation when it has an id."""
        session, tokens = self._prepare()
        state = state or ConversationState()
        reqid = self._reqid
        self._reqid += _REQID_STEP
        request = build_generate_request(
            text,
            state,
            tokens=tokens,
            access_token=self._access_token(),
            model_id=self._model_id,
            model_number=self._model_number,
            extended_thinking=self._extended_thinking,
            language=tokens.language,
            reqid=reqid,
            session_uuid=self._session_uuid,
        )
        response = session.post(
            request.url,
            params=request.params,
            headers=request.headers,
            data=request.data,
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise GeminiError(
                f"Gemini StreamGenerate returned HTTP {response.status_code}."
            )
        result = parse_stream(response.text)
        if state.is_new():
            logger.info("Gemini started conversation %s", result.state.cid)
        return result


_CLIENT: GeminiWebClient | None = None


def get_client() -> GeminiWebClient:
    """The process-wide client (warm containers reuse cookies and tokens)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = GeminiWebClient()
    return _CLIENT
