import json
import logging

from deepl import Translator

from .common_utils import read_ssm_param
from .session import EngineResponder, run_engine_event
from .user_context import UserContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

auth_key = read_ssm_param(param_name="DEEPL_AUTHKEY")
translator = Translator(auth_key)


class DeepLResponder(EngineResponder):
    label = "deepl"
    wants_session = False  # translations persist nothing
    reply_on_error = True  # a failing language is replied as error text
    format = "plain"  # translations are literal text, never Markdown

    def answer(
        self,
        payload: dict,
        context: UserContext | None,
    ) -> list:
        """Translate into every requested language; one result per language."""
        languages = payload["languages"].upper().split(",")
        results = []
        for lang in languages:
            try:
                response = translator.translate_text(
                    payload["text"].replace("/tr", ""),
                    target_lang=lang.strip(),
                )
                result = response.text
            except Exception as e:  # any per-language failure becomes an error reply
                logger.error("Translation to %s failed", lang, exc_info=e)
                result = str(e)
            results.append((lang.strip(), result))
        return results


_RESPONDER = DeepLResponder()


def sns_handler(event, context):
    """AWS SNS event handler for the DeepL engine Lambda."""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        run_engine_event(payload, request_id, _RESPONDER)
