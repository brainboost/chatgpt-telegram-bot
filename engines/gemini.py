"""Gemini engine: the web chat backend, not the API.

Authentication is a replayed browser session (``__Secure-1PSID`` cookies) against
``gemini.google.com``, the same backend the Gemini web app talks to. That is a
deliberate trade: it puts the bot on the app's free-tier allowance — compute-based
limits that refresh every five hours — instead of the API key's own quota, which
is what the previous implementation hit limits on.

The cost of that trade is that history is not ours to replay. Payload slot 0
carries a single message, so the previous approach (rebuilding the transcript
from stored turns) is impossible; instead the thread lives on Google's side and
we persist the identifiers that address it in the user context's ``session``
blob. ``/reset`` clears that blob like any other memory.

Because those identifiers can go stale — Google rejecting a thread, throttling
it, or a concurrent update overwriting the row — a follow-up that fails is retried
once as a new conversation rather than being retried forever. See ``_run_turn``.

Failures here are ordinary ``GeminiError`` subclasses, so the shared runtime's
failover chain advances to the next chat provider — including when the free-tier
quota for the current window is spent, which arrives as HTTP 200 with an error
code inside the stream rather than an HTTP error status.
"""

import json
import logging

from .gemini_web import (
    ConversationState,
    CredentialsRejectedError,
    GeminiError,
    TemporarilyBlockedError,
    TurnResult,
    UsageLimitError,
    get_client,
)
from .session import EngineResponder, run_engine_event
from .user_context import UserContext

logger = logging.getLogger(__name__)

# A quota, an IP block or dead credentials apply to the whole account, so
# retrying a different conversation cannot help. Every other failure might be
# specific to the stored thread, so it is worth retrying freshly.
_ACCOUNT_WIDE_ERRORS = (
    UsageLimitError,
    TemporarilyBlockedError,
    CredentialsRejectedError,
)


class GeminiResponder(EngineResponder):
    label = "gemini"
    wants_session = True
    reply_on_error = False  # failover intercepts errors; the chain tail replies
    fails_over = True  # provider failures advance the chat failover chain

    def answer(self, payload: dict, context: UserContext | None) -> str:
        text = payload.get("text", "")
        state = (
            ConversationState.from_dict(context.session)
            if context is not None
            else ConversationState()
        )
        result = _run_turn(text, state, context)
        if context is not None:
            # Persisted by the runtime's add_turn/persist after this returns.
            context.set_session(result.state.to_dict())
        # Raw provider content; the result path renders it for Telegram.
        return result.text


def _run_turn(
    text: str, state: ConversationState, context: UserContext | None
) -> TurnResult:
    """Run one turn, recovering from a thread the backend will not continue.

    A stored conversation can stop being continuable — Google rejects or throttles
    it, or the ids go stale after a concurrent update to the same row. Retrying
    the same ids on every later message would leave the thread permanently dead
    while the failover chain answered from another provider, so the user would
    silently lose Gemini for the rest of that conversation. Instead, drop the dead
    thread and answer this turn as a new one.
    """
    try:
        return generate(text, state)
    except GeminiError as error:
        if state.is_new() or isinstance(error, _ACCOUNT_WIDE_ERRORS):
            raise
        logger.warning(
            "Gemini will not continue conversation %s; dropping the thread and "
            "retrying as a new conversation",
            state.cid,
            exc_info=error,
        )
        if context is not None:
            context.set_session({})
            context.persist()  # forget the dead thread before retrying
        return generate(text, ConversationState())


def generate(text: str, state: ConversationState) -> TurnResult:
    """Run one turn against the web backend.

    This is the module's provider-I/O seam: tests replace it to exercise the
    responder without network or credentials.
    """
    return get_client().ask(text, state)


_RESPONDER = GeminiResponder()


def sns_handler(event, context):
    """AWS SNS event handler for the Gemini engine Lambda."""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        run_engine_event(payload, request_id, _RESPONDER)
