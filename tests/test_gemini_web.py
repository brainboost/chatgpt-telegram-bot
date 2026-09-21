"""Tests for the Gemini web wire protocol.

Fixtures are encoded exactly the way Google frames a response — ``)]}'`` then
``<length>`` / ``<json>`` line pairs — so the parser is exercised against the
real shape rather than a convenient one.
"""

import json

import pytest

from engines.gemini_auth import BootstrapTokens, GeminiCredentials
from engines.gemini_web import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_NUMBER,
    MODEL_HEADER_KEY,
    REQUEST_UUID_HEADER,
    STREAM_URL,
    ConversationState,
    GeminiError,
    GeminiWebClient,
    RequestRejectedError,
    StreamAbortedError,
    TemporarilyBlockedError,
    TurnResult,
    UsageLimitError,
    build_generate_request,
    build_payload,
    parse_stream,
    strip_annotations,
)

NEW_METADATA = ["", "", "", None, None, None, None, None, None, ""]
TOKENS = BootstrapTokens(
    access_token="scraped-token",
    build_label="boq_assistant-bard-web-server_20260920.06_p0",
    session_id="-866040998427962646",
    language="en",
)


def framed(*payloads: object) -> str:
    """Encode payloads the way Google frames a chunked response."""
    lines = [")]}'", ""]
    for payload in payloads:
        raw = json.dumps(payload)
        lines.append(str(len(raw)))
        lines.append(raw)
    return "\n".join(lines)


def result_frame(
    text: str,
    *,
    cid: str = "c_1",
    rid: str = "r_2",
    rcid: str = "rc_3",
    complete: bool = True,
    context: str | None = None,
) -> list:
    candidate: list = [None] * 9
    candidate[0] = rcid
    candidate[1] = [text]
    candidate[8] = [2 if complete else 1]
    inner: list = [None] * 26
    inner[1] = [cid, rid]
    inner[4] = [candidate]
    if context is not None:
        inner[25] = context
    return [["wrb.fr", None, json.dumps(inner)]]


BARD_ERROR = "type.googleapis.com/assistant.boq.bard.application.BardErrorInfo"


def error_frame(code: int) -> list:
    """A BardErrorInfo frame, exactly as Google wraps it."""
    return [["wrb.fr", None, None, None, None, [13, None, [[BARD_ERROR, [code]]]]]]


def inner_of(payload: str) -> list:
    return json.loads(json.loads(payload)[1])


def test_first_turn_sends_the_new_conversation_metadata():
    inner = inner_of(build_payload("hello", ConversationState()))

    assert len(inner) == 81
    assert inner[0] == ["hello", 0, None, None, None, None, 0]
    assert inner[1] == ["en"]
    assert inner[2] == NEW_METADATA


def test_followup_turn_sends_the_conversation_identifiers():
    state = ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx")
    inner = inner_of(build_payload("hi", state))

    assert inner[2] == ["c_1", "r_2", "rc_3", None, None, None, None, None, None, "ctx"]


def test_deep_research_slot_stays_unset():
    inner = inner_of(build_payload("hi", ConversationState()))

    assert inner[3] is None
    assert inner[4] is None


def test_payload_carries_the_model_number_and_thinking_level():
    inner = inner_of(build_payload("hi", ConversationState()))

    assert inner[79] == DEFAULT_MODEL_NUMBER
    assert inner[80] == 2  # extended thinking on
    assert inner[4] is None  # Deep Research uuid stays unset


def test_request_uuid_is_mirrored_into_the_body():
    inner = inner_of(build_payload("hi", ConversationState(), request_uuid="REQ-1"))

    assert inner[59] == "REQ-1"


def test_outer_envelope_is_a_null_then_a_json_string():
    outer = json.loads(build_payload("hi", ConversationState()))

    assert outer[0] is None
    assert isinstance(outer[1], str)


def test_parse_stream_returns_the_answer_and_the_next_turn_state():
    raw = framed(result_frame("the answer", context="ctx-1"))

    result = parse_stream(raw)

    assert result.text == "the answer"
    assert result.state == ConversationState(
        cid="c_1", rid="r_2", rcid="rc_3", context="ctx-1"
    )


def test_parse_stream_keeps_the_longest_delta_of_a_candidate():
    raw = framed(
        result_frame("par", context="ctx"),
        result_frame("partial answer", context="ctx"),
    )

    assert parse_stream(raw).text == "partial answer"


def test_parse_stream_prefers_the_first_candidate():
    first: list = [None] * 9
    first[0], first[1], first[8] = "rc_a", ["primary"], [2]
    second: list = [None] * 9
    second[0], second[1], second[8] = "rc_b", ["a much longer secondary answer"], [2]
    inner: list = [None] * 26
    inner[1] = ["c_1", "r_2"]
    inner[4] = [first, second]

    assert parse_stream(framed([["wrb.fr", None, json.dumps(inner)]])).text == "primary"


def test_parse_stream_ignores_the_length_marker_and_a_mangled_prefix():
    """A pasted capture can lose the `)]}'` marker; frames must still parse."""
    raw = ")\n]\n}\n'\n\n99\n" + json.dumps(result_frame("ok", context="ctx"))

    assert parse_stream(raw).text == "ok"


def test_parse_stream_skips_junk_and_bookkeeping_frames():
    raw = framed(
        [["di", 198], ["af.httprm", 197, "-1460015816955467607", 6]],
        [["wrb.fr", None, None]],
        result_frame("real answer", context="ctx"),
    )

    assert parse_stream(raw).text == "real answer"


def test_parse_stream_raises_when_the_stream_carries_no_answer():
    raw = framed([["wrb.fr", None, None]], [["e", 4, None, None, 132]])

    with pytest.raises(StreamAbortedError):
        parse_stream(raw)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (1037, UsageLimitError),
        (1060, TemporarilyBlockedError),
        (1050, GeminiError),
    ],
)
def test_in_stream_error_codes_are_classified(code, expected):
    frame = ["wrb.fr", None, None, None, None, [None, None, [[None, [code]]]]]
    raw = framed([frame])

    with pytest.raises(expected):
        parse_stream(raw)


def test_a_rejected_request_is_reported_as_expired_credentials():
    raw = framed([["wrb.fr", None, None, None, None, [7]]])

    with pytest.raises(RequestRejectedError) as error:
        parse_stream(raw)

    assert "cookies" in str(error.value)


def test_a_trailing_error_after_a_complete_answer_is_ignored():
    """Google appends codes such as 1096 *after* a finished turn.

    Verified live: an answer with completion marker 2, a conversation title, and
    then a BardErrorInfo frame. Raising here would discard a good reply.
    """
    raw = framed(result_frame("probe ok", context="ctx"), error_frame(1096))

    result = parse_stream(raw)

    assert result.text == "probe ok"
    assert result.state.cid == "c_1"


def test_an_error_code_is_still_fatal_when_no_answer_arrived():
    with pytest.raises(UsageLimitError):
        parse_stream(framed(error_frame(1037)))


@pytest.mark.parametrize("code", [1096, 1097])
def test_an_unknown_trailing_error_does_not_cost_a_good_answer(code):
    assert parse_stream(framed(result_frame("still fine", context="c"), error_frame(code))).text == "still fine"


def test_strips_the_followup_suggestion():
    text = 'Answer body.\n<FollowUp label="More?" query="Tell me more"/>'

    assert strip_annotations(text) == "Answer body."


def test_strips_googleusercontent_artifacts():
    text = "Answer body.http://googleusercontent.com/whatever/1\n"

    assert strip_annotations(text) == "Answer body."


def test_annotations_are_stripped_on_accumulated_text():
    raw = framed(
        result_frame('<FollowUp label="x" query="y"/>', context="ctx"),
        result_frame('Real answer.\n<FollowUp label="x" query="y"/>', context="ctx"),
    )

    assert parse_stream(raw).text == "Real answer."


def test_conversation_state_roundtrips_through_a_dict():
    state = ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx")

    assert ConversationState.from_dict(state.to_dict()) == state


def test_a_new_conversation_persists_as_an_empty_dict():
    assert ConversationState().to_dict() == {}
    assert ConversationState.from_dict(None) == ConversationState()


def test_generate_request_carries_tokens_params_and_headers():
    request = build_generate_request(
        "hi",
        ConversationState(),
        tokens=TOKENS,
        access_token="scraped-token",
        reqid=123456,
        session_uuid="SESSION-UUID",
        request_uuid="REQ-1",
    )

    assert request.url == STREAM_URL
    assert request.params == {
        "hl": "en",
        "_reqid": "123456",
        "rt": "c",
        "bl": "boq_assistant-bard-web-server_20260920.06_p0",
        "f.sid": "-866040998427962646",
    }
    assert request.data["at"] == "scraped-token"
    assert json.loads(request.headers[REQUEST_UUID_HEADER]) == ["REQ-1", 1]
    header = json.loads(request.headers[MODEL_HEADER_KEY])
    assert header[4] == DEFAULT_MODEL_ID
    assert header[11] == 1  # free tier
    assert header[16] == "SESSION-UUID"


def test_generate_request_omits_bl_and_fsid_when_unknown():
    tokens = BootstrapTokens(access_token=None)

    request = build_generate_request("hi", ConversationState(), tokens=tokens, access_token=None)

    assert "bl" not in request.params
    assert "f.sid" not in request.params
    assert request.data["at"] == ""


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Stands in for a curl_cffi session; records the POST it received."""

    def __init__(self, response):
        self.response = response
        self.posted = None

    def post(self, url, *, params, headers, data, timeout):
        self.posted = {
            "url": url,
            "params": params,
            "headers": headers,
            "data": data,
            "timeout": timeout,
        }
        return self.response


def _client(response, *, tokens, credentials=None):
    session = FakeSession(response)
    client = GeminiWebClient(
        credentials=credentials
        or GeminiCredentials(cookies={"__Secure-1PSID": "psid"}),
        session=session,
        token_fetcher=lambda _session: tokens,
        token_saver=lambda _token: False,  # never touch the bucket in tests
    )
    return client, session


def test_client_sends_a_scraped_token_and_parses_the_answer():
    client, session = _client(
        FakeResponse(framed(result_frame("hi there", context="ctx"))), tokens=TOKENS
    )

    result = client.ask("hello")

    assert result == TurnResult(
        text="hi there",
        state=ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx"),
    )
    assert session.posted["data"]["at"] == "scraped-token"
    assert session.posted["params"]["f.sid"] == "-866040998427962646"


def test_client_falls_back_to_the_cached_token_when_the_scrape_misses():
    credentials = GeminiCredentials(
        cookies={"__Secure-1PSID": "psid"}, access_token="cached-token"
    )
    client, session = _client(
        FakeResponse(framed(result_frame("ok", context="ctx"))),
        tokens=BootstrapTokens(build_label="b", session_id="s"),
        credentials=credentials,
    )

    client.ask("hello")

    assert session.posted["data"]["at"] == "cached-token"


def test_client_prefers_the_freshly_scraped_token_over_the_cached_one():
    credentials = GeminiCredentials(
        cookies={"__Secure-1PSID": "psid"}, access_token="expired-token"
    )
    client, session = _client(
        FakeResponse(framed(result_frame("ok", context="ctx"))),
        tokens=TOKENS,
        credentials=credentials,
    )

    client.ask("hello")

    assert session.posted["data"]["at"] == TOKENS.access_token


def test_client_continues_an_existing_conversation():
    client, session = _client(
        FakeResponse(framed(result_frame("again", context="ctx-2"))), tokens=TOKENS
    )
    state = ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx-1")

    result = client.ask("follow up", state)

    inner = inner_of(session.posted["data"]["f.req"])
    assert inner[2] == ["c_1", "r_2", "rc_3", None, None, None, None, None, None, "ctx-1"]
    assert result.state.context == "ctx-2"


def test_client_reports_a_non_200_status():
    client, _ = _client(FakeResponse("", status_code=500), tokens=TOKENS)

    with pytest.raises(GeminiError) as error:
        client.ask("hello")

    assert "500" in str(error.value)


def test_client_increments_reqid_between_turns():
    client, session = _client(
        FakeResponse(framed(result_frame("a", context="c"))), tokens=TOKENS
    )

    client.ask("one")
    first = int(session.posted["params"]["_reqid"])
    client.ask("two")
    second = int(session.posted["params"]["_reqid"])

    assert second - first == 100_000


def test_accept_language_header_is_well_formed():
    """A scraped locale like "en-US" must not become "en-US-US"."""
    request = build_generate_request(
        "hi",
        ConversationState(),
        tokens=BootstrapTokens(access_token="t", language="en"),
        access_token="t",
    )

    assert request.headers["accept-language"] == "en-US,en;q=0.9"


def test_client_caches_a_scraped_token_for_the_next_cold_start():
    saved = []
    session = FakeSession(FakeResponse(framed(result_frame("ok", context="ctx"))))
    client = GeminiWebClient(
        credentials=GeminiCredentials(cookies={"__Secure-1PSID": "psid"}),
        session=session,
        token_fetcher=lambda _session: TOKENS,
        token_saver=saved.append,
    )

    client.ask("hi")

    assert saved == [TOKENS.access_token]


def test_client_does_not_rewrite_an_unchanged_cached_token():
    saved = []
    credentials = GeminiCredentials(
        cookies={"__Secure-1PSID": "psid"}, access_token=TOKENS.access_token
    )
    client = GeminiWebClient(
        credentials=credentials,
        session=FakeSession(FakeResponse(framed(result_frame("ok", context="ctx")))),
        token_fetcher=lambda _session: TOKENS,
        token_saver=saved.append,
    )

    client.ask("hi")

    assert saved == []
