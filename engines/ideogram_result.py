"""Ideogram result polling: one SQS-delayed check per invocation.

The wait between checks lives in the SQS delivery delay, not in this process:
each queue message triggers exactly one poll, and while the images are not ready
the handler re-queues the request with a backoff delay and returns. Nothing runs
(or is billed) between checks.

Two properties this module deliberately keeps:

- **No credentials on the wire.** The queue message carries the result id and
  routing fields only; the session cookie is read from the bucket here (see
  :func:`engines.ideogram_img.authenticated_headers`). A poison message sitting
  in the DLQ therefore holds no session material.
- **Bounded polling.** ``attempt`` rides in the payload, the delay grows, and
  after ``MAX_POLLS`` the user is told the generation timed out instead of the
  chain polling forever.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import boto3
from curl_cffi import requests

from .ideogram_img import authenticated_headers
from .session import publish_result

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

engine_type = "ideogram"
threshold_img_quality = 1024
base_url = "https://ideogram.ai"
browser_version = "chrome120"
retrieve_metadata_url = f"{base_url}/api/images/retrieve_metadata_request_id/"
get_images_url = f"{base_url}/api/images/direct/"

#: Poll budget per request: the attempt counter starts at 0, each "not ready"
#: schedules the next poll after RETRY_DELAYS[attempt], and the last attempt
#: gives up. Delays sum to ~80s of waiting plus the polls themselves.
MAX_POLLS = 8
RETRY_DELAYS = (5, 5, 10, 10, 15, 15, 20, 20)

TOO_LONG_MESSAGE = (
    "The image generation is taking longer than expected, so I stopped waiting "
    "for it. Please try /imagine again in a moment."
)

_sqs = None
_queue_url: str | None = None


class IdeogramError(Exception):
    """A failed Ideogram result poll; raised so the DLQ can catch it."""


@dataclass(frozen=True)
class PollOutcome:
    """What one poll decided: ready, poll again later, or give up."""

    urls: str | None = None
    retry_in: int | None = None
    attempt: int = 0
    gave_up: bool = False


def is_ready(metadata: dict) -> bool:
    """Ideogram reports a resolution once the images exist."""
    resolution = metadata.get("resolution")
    return bool(resolution) and resolution >= threshold_img_quality


def retry_delay(attempt: int) -> int:
    """Seconds to wait after ``attempt`` failed polls."""
    return RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]


def _image_urls(metadata: dict) -> str:
    return "\n".join(
        get_images_url + item["response_id"] for item in metadata.get("responses", [])
    )


def evaluate_poll(payload: dict, metadata: dict) -> PollOutcome:
    """Decide the next step from the API metadata (pure, no I/O)."""
    attempt = int(payload.get("attempt", 0))
    if is_ready(metadata):
        return PollOutcome(urls=_image_urls(metadata), attempt=attempt)
    next_attempt = attempt + 1
    if next_attempt >= MAX_POLLS:
        return PollOutcome(attempt=next_attempt, gave_up=True)
    return PollOutcome(retry_in=retry_delay(attempt), attempt=next_attempt)


def fetch_metadata(payload: dict) -> dict:
    """One metadata poll against Ideogram (raises :class:`IdeogramError`)."""
    result_id = payload.get("result_id")
    if not result_id:
        raise IdeogramError("Cannot get result_id")
    logger.info("Retrieving images (result_id=%s)", result_id)
    response = requests.get(
        url=retrieve_metadata_url + result_id,
        headers=authenticated_headers(),
        impersonate=browser_version,
    )
    if not response.ok:
        logger.info(response)
        raise IdeogramError(f"Cannot retrieve images for result_id {result_id}")
    return response.json()


def result_queue_url(payload: dict) -> str:
    """Where the next poll goes: the payload's queue, or ours by name."""
    global _sqs, _queue_url
    if payload.get("queue_url"):
        return payload["queue_url"]
    if _queue_url is None:
        _sqs = boto3.session.Session().client("sqs")
        _queue_url = _sqs.get_queue_url(QueueName="Ideogram-Result-Queue")["QueueUrl"]
    return _queue_url


def requeue(payload: dict, *, attempt: int, delay_seconds: int) -> None:
    """Send the next poll as a fresh message, delayed by SQS."""
    global _sqs
    if _sqs is None:
        _sqs = boto3.session.Session().client("sqs")
    logger.info(
        "Result %s is not ready (attempt %s); re-checking in %ss",
        payload.get("result_id"),
        attempt,
        delay_seconds,
    )
    _sqs.send_message(
        QueueUrl=result_queue_url(payload),
        MessageBody=json.dumps({**payload, "attempt": attempt}),
        DelaySeconds=delay_seconds,
    )


def poll_images(
    payload: dict,
    *,
    fetch: Callable[[dict], dict] = fetch_metadata,
    schedule: Callable[..., None] = requeue,
) -> PollOutcome:
    """Poll once: fetch the metadata, then act on :func:`evaluate_poll`."""
    outcome = evaluate_poll(payload, fetch(payload))
    if outcome.retry_in is not None:
        schedule(
            payload,
            attempt=outcome.attempt,
            delay_seconds=outcome.retry_in,
        )
    return outcome


def sqs_handler(event, context):
    """AWS SQS event handler: poll Ideogram results and publish them."""
    logger.info("Request ID: %s", context.aws_request_id)
    for record in event["Records"]:
        payload = json.loads(record["body"])
        outcome = poll_images(payload)
        if outcome.urls:
            publish_result(payload, engine_type, outcome.urls)
        elif outcome.gave_up:
            logger.error(
                "Giving up on result %s after %s polls",
                payload.get("result_id"),
                outcome.attempt,
            )
            # Publish as a text result: the images path would treat this
            # sentence as a photo URL and reply "Error: <sentence>".
            publish_result(
                {**payload, "type": "text"},
                engine_type,
                TOO_LONG_MESSAGE,
                format="plain",
            )
