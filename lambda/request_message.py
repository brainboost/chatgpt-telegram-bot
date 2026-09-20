"""The request message: what the bot publishes to the engine request topic.

This module owns the wire shape of a request to the AI engines and the SNS
MessageAttributes it implies. It is deliberately pure: no python-telegram-bot,
no boto3, no SSM reads at import time — callers pass plain values in and get a
typed model plus the exact (Message, MessageAttributes) pair to publish.

Interface summary (everything a caller must know):

- ``RequestKind`` is the vocabulary used both as the message body discriminator
  (``type``) and as the SNS ``type`` filter attribute. Never hand-write these
  strings elsewhere.
- One class per kind: ``TextRequest``, ``CommandRequest``, ``TranslateRequest``,
  ``IdeogramRequest``. Construct the one matching the message you publish.
- ``to_sns_message(request, engines=None)`` returns ``(body, attributes)``:
  ``body`` is the JSON string to pass as SNS ``Message``; ``attributes`` are the
  ``MessageAttributes``. Pass ``engines`` (list of engine ids from the user's
  configuration) only for kinds that should be fanned out to specific engines
  (``text`` and ``command`` today) — it becomes the ``engines`` String.Array
  filter attribute.
- Unknown keys are ignored on parse (``extra="ignore"``): engine handlers
  re-publish the payload with added fields (``response``, ``engine``) on the
  result topic, which is a different message and must not fail here.

Wire-shape history: ``timestamp``, a body-level ``engines`` list, and the
``file`` key were removed when this module replaced the hand-built dicts — no
consumer read them.
"""

import enum
import json
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestKind(str, enum.Enum):
    """The kind vocabulary shared by the SNS filter and the message body."""

    TEXT = "text"
    COMMAND = "command"
    TRANSLATE = "translate"
    IDEOGRAM = "ideogram"


class BaseRequest(BaseModel):
    """Fields every request carries, regardless of kind."""

    model_config = ConfigDict(extra="ignore")

    user_id: int
    chat_id: int
    username: str | None = None
    message_id: int  # used by the result handler as reply_to_message_id
    update_id: int  # Telegram update id; retained metadata
    text: str


class TextRequest(BaseRequest):
    """A plain chat message (or its transcript / caption) for the user's engines."""

    type: Literal["text"] = "text"
    config: dict[str, Any] | None = None  # user-config snapshot, metadata


class CommandRequest(BaseRequest):
    """A bot command routed to the user's engines (e.g. /reset)."""

    type: Literal["command"] = "command"


class TranslateRequest(BaseRequest):
    """A translation request for DeepL; languages is a comma-separated code list."""

    type: Literal["translate"] = "translate"
    languages: str


class IdeogramRequest(BaseRequest):
    """An image-generation prompt for the Ideogram engine."""

    type: Literal["ideogram"] = "ideogram"
    config: dict[str, Any] | None = None  # user-config snapshot, metadata


RequestMessage = Annotated[
    TextRequest | CommandRequest | TranslateRequest | IdeogramRequest,
    Field(discriminator="type"),
]

_KIND_MODELS = {
    RequestKind.TEXT: TextRequest,
    RequestKind.COMMAND: CommandRequest,
    RequestKind.TRANSLATE: TranslateRequest,
    RequestKind.IDEOGRAM: IdeogramRequest,
}


def parse_request(payload: dict) -> RequestMessage:
    """Parse an inbound request dict into its typed kind.

    Dispatches on the ``type`` key via ``RequestKind``, so an unknown kind
    fails loudly (ValueError) instead of being silently absorbed by another
    kind's ``extra="ignore"``. Only the request fields are validated here —
    result-topic payloads (which add ``response``/``engine``) are a different
    message and must not be parsed with this.
    """
    kind = RequestKind(payload.get("type", ""))
    return _KIND_MODELS[kind].model_validate(payload)


def to_sns_message(
    request: RequestMessage,
    engines: Sequence[str] | None = None,
) -> tuple[str, dict]:
    """Build the SNS (Message, MessageAttributes) pair for one publish.

    ``engines`` is the routing filter for kinds fanned out to specific engines
    (text, command); omit it for kinds routed purely by type (translate,
    ideogram). ``Message`` is the JSON body; ``MessageAttributes`` always carry
    the ``type`` filter and optionally the ``engines`` String.Array filter.
    """
    body = request.model_dump_json(exclude_none=True)
    attrs: dict = {
        "type": {"DataType": "String", "StringValue": request.type},
    }
    if engines:
        attrs["engines"] = {
            "DataType": "String.Array",
            "StringValue": json.dumps(list(engines)),
        }
    return body, attrs
