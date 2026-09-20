import json
import logging
import os

import requests

from .common_utils import read_ssm_param
from .session import EngineResponder, run_engine_event
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

# One shared OpenAI-compatible provider module for all Ollama Cloud engines.
# Each engine Lambda is configured by CDK through environment variables:
#   OLLAMA_ENGINE = engine id used in SNS filters / user configs ("llama", "qwen", ...)
#   OLLAMA_MODEL  = Ollama Cloud model tag (verify available tags on your account at
#                   https://ollama.com/search?c=cloud), e.g. "llama4:maverick-cloud",
#                   "qwen3.5:cloud"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")
engine_type = os.environ.get("OLLAMA_ENGINE", "llama")
model = os.environ.get("OLLAMA_MODEL", "llama4:maverick")
request_timeout = 270  # seconds; engines have a 5-minute Lambda timeout


class OllamaError(Exception):
    """A failed Ollama Cloud request, surfaced to the user as error text."""


class OllamaResponder(EngineResponder):
    label = engine_type
    wants_session = True
    reply_on_error = True  # kept for the responder contract; failover intercepts errors
    fails_over = True  # provider failures advance the chat failover chain

    def __init__(self) -> None:
        self._api_key: str | None = None

    def answer(self, payload: dict, context: UserContext | None) -> str:
        text = payload.get("text", "")
        if self._api_key is None:
            self._api_key = read_ssm_param(param_name="OLLAMA_API_KEY")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        turns = context.turns if context is not None else []
        body = {
            "model": model,
            "messages": _build_messages(text, turns),
            "stream": False,
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        logger.info(
            "Sending request to Ollama model '%s' for user %s",
            model,
            payload.get("user_id"),
        )
        response = requests.post(
            url=f"{OLLAMA_BASE_URL}/chat/completions",
            headers=headers,
            data=json.dumps(body),
            timeout=request_timeout,
        )
        if not response.ok:
            logger.error(
                "Ollama request failed: %s %s %s",
                response.status_code,
                response.reason,
                response.text,
            )
            raise OllamaError(
                f"Ollama ({model}) request returned {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        logger.info("Received %s chars from model '%s'", len(content), model)
        # Raw provider content; the result path renders it for Telegram.
        return content


def _build_messages(text: str, turns: list) -> list:
    """Alternating user/assistant messages from stored turns, then the new text."""
    messages = []
    for turn in turns:
        messages.append({"role": "user", "content": turn["request"]})
        messages.append({"role": "assistant", "content": turn["response"]})
    messages.append({"role": "user", "content": text})
    return messages


_RESPONDER = OllamaResponder()


def sns_handler(event, context):
    """AWS SNS event handler for Ollama Cloud engine Lambdas."""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        run_engine_event(payload, request_id, _RESPONDER)
