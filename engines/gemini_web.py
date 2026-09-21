"""The Gemini web chat wire protocol.

Everything in this module is a transcription of what ``gemini.google.com``
actually exchanges, reverse-engineered from a captured HAR and then replayed
live. See ``docs/gemini-web-protocol.md`` for the evidence and for how to
recapture it when Google changes the format.

Shape of one turn:

1. ``GET /app`` yields the page globals (``at`` token, ``bl`` build label,
   ``f.sid`` session id) — see :mod:`engines.gemini_auth`.
2. ``POST .../StreamGenerate?bl=..&f.sid=..&hl=..&_reqid=..&rt=c`` with a form body
   ``at=<token>&f.req=<json>``. ``f.req`` is a two-element array whose second
   element is a JSON *string* holding an 81-slot payload list.
3. The response is chunked: ``)]}'`` then repeated length/JSON line pairs. Each
   JSON value is an array of frames; a result frame is
   ``["wrb.fr", rpcid, "<inner json string>", ...]``.

Only what a Telegram bot needs is modelled here. The web app's own affordances —
deep research, Gems, temporary chats, uploads, image generation, citations,
thinking blocks, suggested follow-up questions — are deliberately ignored: the
payload slots that drive them stay unset, and the answer text is taken as plain
text.

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
EXTENDED_THINKING = True

DEFAULT_LANGUAGE = "en"
_REQID_START = 100_000
_REQID_STEP = 100_000
_REQUEST_TIMEOUT = 300

# In-stream error codes arrive in frame[5] on an HTTP 200 response.
_USAGE_LIMIT = 1037
_IP_BLOCKED = 1060
_REQUEST_REJECTED = 7

# The tag body tolerates quoted attribute values instead of stopping at the first
# ">", so `label="a > b"` cannot leave a fragment behind in the message.
_FOLLOWUP_RE = re.compile(
    r"\s*<FollowUp\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*/?>(?:\s*</FollowUp>)?",
    re.IGNORECASE,
)


class GeminiError(RuntimeError):
    """The web backend answered, but the turn did not complete."""


class UsageLimitError(GeminiError):
    """Free-tier compute quota for the current five-hour window is spent."""


class TemporarilyBlockedError(GeminiError):
    """Google has temporarily flagged this IP (HTTP 429 or in-stream 1060)."""


class RequestRejectedError(GeminiError):
    """The request was refused — usually dead cookies or an invalid payload."""


class CredentialsRejectedError(RequestRejectedError):
    """The transport refused the credentials (HTTP 400/401/403), not one thread."""


class StreamAbortedError(GeminiError):
    """The stream ended without any answer text."""


_ERROR_MESSAGES: dict[int, tuple[type[GeminiError], str]] = {
    _USAGE_LIMIT: (
        UsageLimitError,
        "Gemini free-tier usage limit reached for this five-hour window.",
    ),
    _IP_BLOCKED: (
        TemporarilyBlockedError,
        "Google temporarily blocked this IP (error 1060).",
    ),
    _REQUEST_REJECTED: (
        RequestRejectedError,
        "Gemini refused the request; the stored cookies are most likely expired.",
    ),
}

# The handful of transport failures that carry a meaning worth distinguishing:
# a flagged IP can be retried later, dead credentials cannot.
_STATUS_ERRORS: dict[int, type[GeminiError]] = {
    400: CredentialsRejectedError,  # missing or expired `at`
    401: CredentialsRejectedError,
    403: CredentialsRejectedError,
    429: TemporarilyBlockedError,
}


def _status_error(status: int) -> GeminiError:
    message = f"Gemini StreamGenerate returned HTTP {status}."
    error_type = _STATUS_ERRORS.get(status)
    if error_type is CredentialsRejectedError:
        return CredentialsRejectedError(
            f"{message} The stored cookies or the cached access token are most "
            "likely expired."
        )
    if error_type is not None:
        return error_type(message)
    return GeminiError(message)


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
    """Remove the web app's suggested-follow-up element from the answer.

    Gemini web appends ``<FollowUp label="…" query="…"/>`` to suggest the next
    question, which would otherwise render verbatim in a Telegram message.
    Stripping happens on the accumulated text because the tag can be split across
    stream deltas.

    ``googleusercontent`` artifact URLs are deliberately *not* stripped: none has
    been observed in a live answer, and the pattern would also delete a link the
    model legitimately cited.
    """
    return _FOLLOWUP_RE.sub("", text).strip()


def build_model_header(model_id: str, *, session_uuid: str = "") -> str:
    """The ``x-goog-ext-525001261-jspb`` value selecting model and tier."""
    header: list = [
        1, None, None, None, model_id, None, None, 0, list(CAPABILITIES),
        None, None, FREE_CAPACITY, None, None, DEFAULT_MODEL_NUMBER,
        2 if EXTENDED_THINKING else 1,
        session_uuid,
    ]
    return json.dumps(header, separators=(",", ":"))


def build_payload(
    text: str,
    state: ConversationState,
    *,
    language: str = DEFAULT_LANGUAGE,
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
    inner[79] = DEFAULT_MODEL_NUMBER
    inner[80] = 2 if EXTENDED_THINKING else 1
    return json.dumps([None, json.dumps(inner)], separators=(",", ":"))


def build_generate_request(
    text: str,
    state: ConversationState,
    *,
    tokens: BootstrapTokens,
    access_token: str | None,
    reqid: int = _REQID_START,
    session_uuid: str = "",
    request_uuid: str | None = None,
) -> GenerateRequest:
    """Assemble the full POST for one turn."""
    language = tokens.language or DEFAULT_LANGUAGE
    request_uuid = request_uuid or str(uuid.uuid4()).upper()

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
            DEFAULT_MODEL_ID, session_uuid=session_uuid
        ),
        REQUEST_UUID_HEADER: json.dumps([request_uuid, 1], separators=(",", ":")),
        **STATIC_HEADERS,
    }
    data = {
        "at": access_token or tokens.access_token or "",
        "f.req": build_payload(
            text, state, language=language, request_uuid=request_uuid
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


def _nested(node: Any, path: tuple[int, ...]) -> Any:
    """Walk a response array, returning None for any missing slot."""
    for index in path:
        if not isinstance(node, list) or len(node) <= index:
            return None
        node = node[index]
    return node


def _frame_error(frame: list) -> int | None:
    """The in-stream error code, if this frame carries one.

    A rejected request puts ``7`` in ``frame[5][0]``; a formatted error puts its
    code at ``frame[5][2][0][1][0]``, wrapped in a ``BardErrorInfo`` record.
    """
    status = _nested(frame, (5,))
    if not isinstance(status, list) or not status:
        return None
    if status[0] == _REQUEST_REJECTED:
        return _REQUEST_REJECTED
    code = _nested(frame, (5, 2, 0, 1, 0))
    return code if isinstance(code, int) else None


def _raise_for_error(code: int) -> None:
    known = _ERROR_MESSAGES.get(code)
    if known is None:
        # Unmapped codes do occur (1096/1097 have been seen after a burst of
        # turns). Log loudly so a persistent one is visible in CloudWatch rather
        # than only showing up as the failover chain quietly taking over.
        logger.error("Gemini returned unmapped in-stream error code %s", code)
        raise GeminiError(f"Gemini returned error code {code}.")
    error_type, message = known
    raise error_type(message)


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

    answer = ""
    for marker in order:
        if texts.get(marker):
            answer = texts[marker]
            break
    if not answer and texts:
        answer = max(texts.values(), key=len)

    if not answer:
        # An error only costs us the turn when it costs us the answer: Google
        # appends codes such as 1096 *after* a complete answer (observed live),
        # and discarding a finished reply would fail the request over for
        # nothing.
        if error is not None:
            _raise_for_error(error)
        raise StreamAbortedError(
            "Gemini ended the stream without an answer (the request may have "
            "been silently aborted)."
        )

    if error is not None:
        logger.warning(
            "Gemini reported code %s after completing the turn; ignoring it", error
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
        token_saver: Callable[..., bool] = save_access_token,
    ) -> None:
        self._credentials = credentials
        self._session = session
        self._token_fetcher = token_fetcher
        self._token_saver = token_saver
        self._tokens: BootstrapTokens | None = None
        self._session_uuid = str(uuid.uuid4()).upper()
        self._reqid = random.randint(10_000, 99_999)

    def _prepare(self) -> tuple[Any, BootstrapTokens]:
        if self._credentials is None:
            self._credentials = load_stored_credentials()
        if self._session is None:
            self._session = new_session(self._credentials)
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

    def _access_token(self) -> str | None:
        if self._tokens and self._tokens.access_token:
            return self._tokens.access_token
        cached = self._credentials.access_token if self._credentials else None
        if cached:
            logger.info("Using the cached Gemini access token")
        return cached

    def _cache_scraped_token(self) -> None:
        """Persist a scraped ``at`` token so the next cold start can reuse it."""
        scraped = self._tokens.access_token if self._tokens else None
        cached = self._credentials.access_token if self._credentials else None
        if not scraped or scraped == cached:
            return
        self._token_saver(scraped)
        if self._credentials is not None:
            self._credentials = replace(self._credentials, access_token=scraped)

    def ask(self, text: str, state: ConversationState | None = None) -> TurnResult:
        """Run one turn, continuing ``state``'s conversation when it has an id.

        The bootstrap triple is cached for the life of the container, but ``at``
        is a session token and ``bl`` is a build label that changes on Google's
        deploys, so a warm container can outlive both. A rejected request is
        retried once after re-scraping — falling back to the cached token — which
        is what lets a long-lived Lambda recover instead of answering 400 until
        it is recycled.
        """
        try:
            return self._turn(text, state)
        except RequestRejectedError as error:
            logger.warning(
                "Gemini rejected the request; re-scraping bootstrap tokens and "
                "retrying once",
                exc_info=error,
            )
            before = self._token_fingerprint()
            self._tokens = None
            self._prepare()
            if self._token_fingerprint() == before:
                # Nothing changed, so resending would repeat the rejected request.
                raise
            return self._turn(text, state)

    def _token_fingerprint(self) -> tuple:
        """What a retry would actually resend, ignoring values that always change.

        ``f.sid`` is generated fresh on every page load, so including it here
        would make each re-scrape look like a change and the guard could never
        fire — least of all in the common case where the page does not expose
        ``at`` at all, which is precisely when resending repeats a dead request.
        ``bl`` is kept: it only changes on Google's deploys, so a new value is a
        real reason to retry.
        """
        tokens = self._tokens
        return (
            self._access_token(),
            tokens.build_label if tokens else None,
        )

    def _turn(self, text: str, state: ConversationState | None) -> TurnResult:
        session, tokens = self._prepare()
        state = state or ConversationState()
        reqid = self._reqid
        self._reqid += _REQID_STEP
        request = build_generate_request(
            text,
            state,
            tokens=tokens,
            access_token=self._access_token(),
            reqid=reqid,
            session_uuid=self._session_uuid,
        )
        response = session.post(
            request.url,
            params=request.params,
            headers=request.headers,
            data=request.data,
            timeout=_REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            raise _status_error(response.status_code)
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
