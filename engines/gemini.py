import json
import logging
import re

from google import genai
from google.genai import types

from .common_utils import read_ssm_param
from .session import EngineResponder, run_engine_event
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

# Current stable Gemini model (GA, Sept 2026). For the pro-tier preview
# instead, use "gemini-3.1-pro-preview".
model = "gemini-3.8-flash"

_client = None
_generation_config = None


class GeminiResponder(EngineResponder):
    label = "gemini"
    wants_session = True
    reply_on_error = False  # provider failures raise to the DLQ

    def answer(self, payload: dict, context: UserContext | None) -> str:
        text = payload.get("text", "")
        if _client is None:
            create()
        turns = context.turns if context is not None else []
        response = _client.models.generate_content_stream(  # ty: ignore[unresolved-attribute]
            model=model,
            contents=_build_contents(text, turns),
            config=_generation_config,
        )
        answer = ""
        for chunk in response:
            if not chunk.parts or chunk.parts[0].text is None:
                continue
            answer += chunk.parts[0].text
        return __as_markdown(answer)


def _build_contents(text: str, turns: list) -> list:
    """Alternating user/model parts from stored turns, then the new text."""
    contents = []
    for turn in turns:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=turn["request"])],
            )
        )
        contents.append(
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=turn["response"])],
            )
        )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    return contents


def create() -> None:
    logger.info("Create chatbot instance")
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_ONLY_HIGH",
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE",
        },
    ]
    global _generation_config
    _generation_config = types.GenerateContentConfig(
        temperature=1.2,
        top_p=0.85,
        max_output_tokens=65534,
        safety_settings=safety_settings,
        response_mime_type="text/plain",
        # response_mime_type="application/json",
    )
    global _client
    api_key = read_ssm_param(param_name="GEMINI_API_KEY")
    _client = genai.Client(api_key=api_key)


def __as_markdown(input: str) -> str:
    input = re.sub(r"(?<!\*)\*(?!\*)", "\\\\*", input)
    input = re.sub(r"\*{2,}", "*", input)
    esc_pattern = re.compile(f"([{re.escape(r'._-+#|{}!=()<>[]')}])")
    return re.sub(esc_pattern, r"\\\1", input)


_RESPONDER = GeminiResponder()


def sns_handler(event, context):
    """AWS SNS event handler for the Gemini engine Lambda."""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        run_engine_event(payload, request_id, _RESPONDER)
