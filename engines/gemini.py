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

Failures here are ordinary ``GeminiError`` subclasses, so the shared runtime's
failover chain advances to the next chat provider — including when the free-tier
quota for the current window is spent, which arrives as HTTP 200 with an error
code inside the stream rather than an HTTP error status.
"""

import json
import logging

from .gemini_web import ConversationState, TurnResult, get_client
from .session import EngineResponder, run_engine_event
from .user_context import UserContext

logger = logging.getLogger(__name__)


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
        result = generate(text, state)
        if context is not None:
            # Persisted by the runtime's add_turn/persist after this returns.
            context.set_session(result.state.to_dict())
        # Raw provider content; the result path renders it for Telegram.
        return result.text


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
