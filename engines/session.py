"""Engine session runtime: intake → session → answer → save → publish.

One implementation of the result pipeline shared by the engine modules, so the
wire contract, the encode step and the publish call exist in exactly one place
instead of being copied into every engine handler.

Interface summary (everything a caller must know):

- An :class:`EngineResponder` supplies what varies per engine: its result
  ``label``, whether it keeps a per-user session (``wants_session``), whether
  provider errors should be replied to the user as error text
  (``reply_on_error``) or raised to the DLQ, and ``answer()`` — provider I/O
  that returns one result (``str``, labelled with ``label``) or several
  (``list[(label, text)]``, e.g. DeepL one per target language).
- ``run_engine_event(payload, request_id, responder)`` owns: command dispatch
  (``type == "command"`` → reset, no publish), the ``/ping`` shortcut (pong,
  not saved to history), session build, the error policy, conversation save
  and result publishing.
- ``build_context`` / ``publish_result`` are the shared helpers for flows that
  don't go through ``run_engine_event`` (the async Ideogram poll handler).

Both ``publish`` and ``context_factory`` are injectable so tests drive the
runtime through its interface without AWS. The real publisher resolves the
result-topic ARN and the boto3 client lazily on first publish, so importing
this module (and the engine modules that use it) has no SSM side effects.
"""

import json
import logging
from collections.abc import Callable
from typing import Protocol

import boto3

from .common_utils import encode_message, escape_markdown_v2, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

_result_topic: str | None = None
_sns = None


class EngineResponder(Protocol):
    """The narrow seam each engine module satisfies."""

    label: str
    wants_session: bool
    reply_on_error: bool

    def answer(
        self,
        payload: dict,
        context: UserContext | None,
    ) -> str | list:
        """Provider I/O. ``str`` = one result under ``label``; a list of
        ``(label, text)`` pairs = several results (e.g. per-language)."""


def run_engine_event(
    payload: dict,
    request_id: str,
    responder: EngineResponder,
    *,
    publish: Callable[[dict], None] | None = None,
    context_factory: Callable[..., UserContext | None] | None = None,
) -> None:
    """Process one engine request payload end-to-end."""
    text = payload.get("text", "")
    kind = payload.get("type", "")

    if kind == "command":
        _handle_command(payload, request_id, responder, context_factory)
        return

    if "/ping" in text:
        logger.info("Answering pong for %s", responder.label)
        publish_result(payload, responder.label, "pong", publish=publish)
        return

    context = (
        _build_session(payload, request_id, responder, context_factory)
        if responder.wants_session
        else None
    )

    try:
        result = responder.answer(payload, context)
    except Exception as e:
        if not responder.reply_on_error:
            raise
        logger.error(
            "Engine '%s' failed for user %s",
            responder.label,
            payload.get("user_id"),
            exc_info=e,
        )
        result = escape_markdown_v2(str(e))

    if isinstance(result, str):
        _save_and_publish(payload, context, responder.label, result, publish)
    else:
        for label, response_text in result:
            publish_result(payload, label, response_text, publish=publish)


def build_context(
    payload: dict,
    request_id: str,
    engine_label: str,
) -> UserContext:
    """Real session: per-user x chat composite key, engine-scoped row."""
    return UserContext(
        user_id=f"{payload['user_id']}_{payload['chat_id']}",
        request_id=request_id,
        engine_id=engine_label,
        username=payload.get("username"),
    )


def publish_result(
    payload: dict,
    engine_label: str,
    text: str,
    *,
    publish: Callable[[dict], None] | None = None,
) -> None:
    """Encode one engine result and publish it on the result topic."""
    result = dict(payload)
    result["engine"] = engine_label
    result["response"] = encode_message(text)
    (publish or _default_publish)(result)


def _handle_command(
    payload: dict,
    request_id: str,
    responder: EngineResponder,
    context_factory: Callable[..., UserContext | None] | None,
) -> None:
    command = payload.get("text", "").removeprefix("/").lower()
    if "reset" in command:
        context = _build_session(payload, request_id, responder, context_factory)
        if context is not None:
            context.reset()
        logger.info(
            "Conversation reset for %s (engine %s)", payload.get("user_id"), responder.label
        )
        return
    logger.error("Unknown command %s", command)


def _build_session(
    payload: dict,
    request_id: str,
    responder: EngineResponder,
    context_factory: Callable[..., UserContext | None] | None,
) -> UserContext | None:
    factory = context_factory or build_context
    return factory(payload, request_id, responder.label)


def _save_and_publish(
    payload: dict,
    context: UserContext | None,
    engine_label: str,
    response_text: str,
    publish: Callable[[dict], None] | None,
) -> None:
    if context is not None:
        context.add_turn(payload.get("text", ""), response_text)
        context.persist()
    publish_result(payload, engine_label, response_text, publish=publish)


def _default_publish(payload: dict) -> None:
    global _result_topic, _sns
    if _sns is None:
        _result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
        _sns = boto3.session.Session().client("sns")
    logger.info("Publishing engine result to topic %s", _result_topic)
    _sns.publish(TopicArn=_result_topic, Message=json.dumps(payload))
