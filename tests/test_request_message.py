"""Tests for the request message module (candidate 1 deepening).

These assert the wire contract chatbot.py now publishes through:
body keys per kind, the SNS type/engines MessageAttributes, and that
unknown keys (added later by engine handlers on the result topic) are
ignored when the model parses a payload back.
"""

import importlib
import json

# The package is literally named `lambda` (a keyword), so it can only be
# imported through a string-based import — same mechanism the Lambda runtime
# uses for handler paths like "lambda.chatbot.telegram_api_handler".
rm = importlib.import_module("lambda.request_message")
CommandRequest = rm.CommandRequest
IdeogramRequest = rm.IdeogramRequest
RequestKind = rm.RequestKind
TextRequest = rm.TextRequest
TranslateRequest = rm.TranslateRequest
to_sns_message = rm.to_sns_message


def _text_request(**overrides):
    return TextRequest(
        user_id=42,
        chat_id=-100123456789,
        username="tester",
        message_id=77,
        update_id=9,
        text="hello engines",
        config={"engines": ["gemini"], "style": "creative"},
        **overrides,
    )


def test_request_kind_vocabulary_matches_wire_values():
    assert RequestKind.TEXT.value == "text"
    assert RequestKind.COMMAND.value == "command"
    assert RequestKind.TRANSLATE.value == "translate"
    assert RequestKind.IDEOGRAM.value == "ideogram"


def test_text_request_wire_shape_and_attributes():
    request = _text_request()
    body, attrs = to_sns_message(request, engines=["gemini", "llama"])

    payload = json.loads(body)
    assert payload["type"] == "text"
    assert payload["text"] == "hello engines"
    assert payload["config"]["style"] == "creative"
    # dropped keys never reappear on the wire
    assert "timestamp" not in payload
    assert "file" not in payload
    assert "engines" not in payload  # body stays free of the routing list

    assert attrs["type"] == {"DataType": "String", "StringValue": "text"}
    assert json.loads(attrs["engines"]["StringValue"]) == ["gemini", "llama"]


def test_command_request_body_and_targeting():
    request = CommandRequest(
        user_id=42,
        chat_id=-100123456789,
        username="tester",
        message_id=77,
        update_id=9,
        text="/reset",
    )
    body, attrs = to_sns_message(request, engines=["gemini"])

    payload = json.loads(body)
    assert payload == {
        "type": "command",
        "user_id": 42,
        "chat_id": -100123456789,
        "username": "tester",
        "message_id": 77,
        "update_id": 9,
        "text": "/reset",
    }
    assert json.loads(attrs["engines"]["StringValue"]) == ["gemini"]


def test_translate_request_carries_languages_no_engines_attr():
    request = TranslateRequest(
        user_id=42,
        chat_id=-100123456789,
        username="tester",
        message_id=77,
        update_id=9,
        text="cześć",
        languages="pl,en-gb",
    )
    body, attrs = to_sns_message(request)

    payload = json.loads(body)
    assert payload["type"] == "translate"
    assert payload["languages"] == "pl,en-gb"
    assert "config" not in payload
    assert "engines" not in attrs  # routed purely by type
    assert attrs["type"] == {"DataType": "String", "StringValue": "translate"}


def test_ideogram_request():
    request = IdeogramRequest(
        user_id=42,
        chat_id=-100123456789,
        username="tester",
        message_id=77,
        update_id=9,
        text="cute kitty with a yarn ball",
        config={"engines": ["gemini"]},
    )
    body, attrs = to_sns_message(request)

    payload = json.loads(body)
    assert payload["type"] == "ideogram"
    assert payload["text"] == "cute kitty with a yarn ball"
    assert "engines" not in attrs


def test_discriminated_union_roundtrip():
    requests = [
        _text_request(),
        CommandRequest(
            user_id=42, chat_id=-100123456789, username="tester",
            message_id=77, update_id=9, text="/reset",
        ),
        TranslateRequest(
            user_id=42, chat_id=-100123456789, username="tester",
            message_id=77, update_id=9, text="cześć", languages="pl",
        ),
        IdeogramRequest(
            user_id=42, chat_id=-100123456789, username="tester",
            message_id=77, update_id=9, text="a prompt", config=None,
        ),
    ]
    for request in requests:
        payload = json.loads(request.model_dump_json(exclude_none=True))
        parsed = rm.parse_request(payload)
        assert parsed == request
        assert isinstance(parsed, type(request))


def test_unknown_keys_are_ignored_on_parse():
    # engine handlers re-publish the payload with extra result fields;
    # parsing such a dict must not fail and must drop the extras.
    payload = json.loads(_text_request().model_dump_json(exclude_none=True))
    payload["response"] = "b64-encoded"
    payload["engine"] = "gemini"
    parsed = rm.parse_request(payload)
    assert parsed == _text_request()


def test_old_shape_with_extra_keys_still_parses():
    # an old hand-built envelope (with timestamp) parses fine today.
    old_envelope = {
        "type": "text",
        "user_id": 42,
        "chat_id": -100123456789,
        "username": "tester",
        "update_id": 9,
        "message_id": 77,
        "text": "hello",
        "timestamp": 1777777777,
        "config": {"engines": ["gemini"]},
    }
    parsed = rm.parse_request(old_envelope)
    assert isinstance(parsed, TextRequest)
    assert parsed.text == "hello"
