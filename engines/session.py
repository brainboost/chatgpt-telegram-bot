"""Engine session runtime: intake → session → answer → save → publish.

One implementation of the result pipeline shared by the engine modules, so the
wire contract, the encode step and the publish call exist in exactly one place
instead of being copied into every engine handler.

Interface summary (everything a caller must know):

- An :class:`EngineResponder` supplies what varies per engine: its result
  ``label``, whether it keeps a per-user session (``wants_session``), whether
  provider errors should be replied to the user as error text
  (``reply_on_error``) or raised to the DLQ, the content ``format`` its
  answers carry (undeclared by default, in which case
  ``providers.DEFAULT_CONTENT_FLAVOR`` applies — the Telegram-side renderer keys
  off it), and ``answer()`` — provider I/O
  that returns one result (``str``, labelled with ``label``) or several
  (``list[(label, text)]``, e.g. DeepL one per target language).
  Chat responders also set ``fails_over``: when their ``answer()`` raises, the
  runtime re-publishes the request to the next provider in the catalog chain
  instead of erroring; at the chain tail the user gets an error reply.
- ``run_engine_event(payload, request_id, responder)`` owns: command dispatch
  (``type == "command"`` → reset, no publish), the ``/ping`` shortcut (pong,
  not saved to history), session build, the failover/error policy,
  conversation save and result publishing.
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

import providers

from .common_utils import encode_message, read_ssm_param
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

_result_topic: str | None = None
_sns = None
_request_topic: str | None = None
_sns_request = None


class EngineResponder(Protocol):
    """The narrow seam each engine module satisfies.

    ``label``, ``wants_session`` and ``reply_on_error`` are class attributes;
    ``format`` is an optional class attribute naming the content flavor of the
    answers (undeclared falls back to ``providers.DEFAULT_CONTENT_FLAVOR``) and
    ``fails_over`` is an optional class attribute (default ``False``) that
    opts a chat responder into the failover chain.
    """

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
    republish: Callable[[dict, str], None] | None = None,
    chain: Callable[[str], str | None] | None = None,
) -> None:
    """Process one engine request payload end-to-end.

    ``republish`` is the failover seam: ``(payload, next_provider_id)`` re-routes
    a failed chat request to the next provider in the chain (the default
    publishes to the request topic with the SNS ``engines`` attribute).
    ``chain`` resolves the next provider from a label and defaults to the
    provider catalog.
    """
    text = payload.get("text", "")
    kind = payload.get("type", "")
    flavor = _responder_format(responder)

    if kind == "command":
        _handle_command(payload, request_id, responder, context_factory)
        return

    if "/ping" in text:
        logger.info("Answering pong for %s", responder.label)
        publish_result(
            payload, responder.label, "pong", publish=publish, format=flavor
        )
        return

    context = (
        _build_session(payload, request_id, responder, context_factory)
        if responder.wants_session
        else None
    )

    try:
        result = responder.answer(payload, context)
    except Exception as e:
        handled = _handle_answer_error(
            payload,
            responder,
            e,
            publish=publish,
            republish=republish,
            chain=chain,
        )
        if handled:
            return
        if not responder.reply_on_error:
            raise
        logger.error(
            "Engine '%s' failed for user %s",
            responder.label,
            payload.get("user_id"),
            exc_info=e,
        )
        result = str(e)

    if isinstance(result, str):
        _save_and_publish(payload, context, responder.label, result, publish, flavor)
    else:
        for label, response_text in result:
            publish_result(
                payload,
                label,
                response_text,
                publish=publish,
                format=flavor,
            )


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
    format: str | None = None,
) -> None:
    """Encode one engine result and publish it on the result topic.

    ``format`` declares the content flavor of ``text`` (see the Telegram-side
    formatting module); when omitted the result carries no declaration and the
    sender falls back to its default.
    """
    result = dict(payload)
    result["engine"] = engine_label
    if format is not None:
        result["format"] = format
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
    format: str,
) -> None:
    if context is not None:
        context.add_turn(payload.get("text", ""), response_text)
        context.persist()
    publish_result(payload, engine_label, response_text, publish=publish, format=format)


def _responder_format(responder: EngineResponder) -> str:
    """The declared content flavor; responders may leave it undeclared.

    ``providers.DEFAULT_CONTENT_FLAVOR`` is authoritative rather than a local
    literal. A declared format travels on the wire and *overrides* the sender's
    own default, so a responder that declares nothing must still name the flavor
    the sender would have chosen — otherwise the sender's default is unreachable
    and its renderer silently never runs.
    """
    declared = getattr(responder, "format", None)
    return declared or providers.DEFAULT_CONTENT_FLAVOR


def _handle_answer_error(
    payload: dict,
    responder: EngineResponder,
    error: Exception,
    *,
    publish: Callable[[dict], None] | None,
    republish: Callable[[dict, str], None] | None,
    chain: Callable[[str], str | None] | None,
) -> bool:
    """Apply the failover policy after ``answer()`` raised.

    Returns True when the failure was fully handled — the request was
    re-published to the next provider in the chain, or the chain is exhausted
    and the tail provider replied a user-facing error. Returns False when the
    caller's ``reply_on_error`` / DLQ policy applies (non-chat responders).
    """
    if not getattr(responder, "fails_over", False):
        return False
    next_provider = (chain or providers.chain_next)(responder.label)
    if next_provider:
        logger.warning(
            "Provider '%s' failed for user %s; failing over to '%s'",
            responder.label,
            payload.get("user_id"),
            next_provider,
            exc_info=error,
        )
        (republish or _default_republish)(payload, next_provider)
        return True
    logger.error(
        "Chat failover chain exhausted at '%s' for user %s",
        responder.label,
        payload.get("user_id"),
        exc_info=error,
    )
    message = (
        "All chat providers failed to answer. "
        f"Last error from {providers.provider_label(responder.label)}: {error}"
    )
    publish_result(payload, responder.label, message, publish=publish, format="plain")
    return True


def _default_republish(payload: dict, next_provider_id: str) -> None:
    """Publish the same request to the next provider's SNS filter."""
    global _request_topic, _sns_request
    if _sns_request is None:
        _request_topic = read_ssm_param(param_name="REQUESTS_SNS_TOPIC_ARN")
        _sns_request = boto3.session.Session().client("sns")
    attrs = {
        "type": {
            "DataType": "String",
            "StringValue": str(payload.get("type", "text")),
        },
        "engines": {
            "DataType": "String.Array",
            "StringValue": json.dumps([next_provider_id]),
        },
    }
    logger.info("Failing over to provider %s", next_provider_id)
    _sns_request.publish(
        TopicArn=_request_topic,
        Message=json.dumps(payload),
        MessageAttributes=attrs,
    )


def _default_publish(payload: dict) -> None:
    global _result_topic, _sns
    if _sns is None:
        _result_topic = read_ssm_param(param_name="RESULT_SNS_TOPIC_ARN")
        _sns = boto3.session.Session().client("sns")
    logger.info("Publishing engine result to topic %s", _result_topic)
    _sns.publish(TopicArn=_result_topic, Message=json.dumps(payload))
