import json
import logging
from datetime import UTC, datetime
from typing import Any

import boto3
import jwt
from botocore.exceptions import BotoCoreError, ClientError
from curl_cffi import requests

from .common_utils import (
    read_json_from_s3,
    read_ssm_param,
    save_to_s3,
)
from .ideogram_cookies import cookie_header, session_cookie
from .ideogram_request import IdeogramImageRequest

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

ig_cookies = "ig-cookies.json"
base_url = "https://ideogram.ai"
browser_version = "chrome120"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
id_key = "AIzaSyBwq4bRiOapXYaKE-0Y46vLAw1-fzALq7Y"
tokens_file = "google_auth.json"
post_task_url = f"{base_url}/api/images/sample"
result_queue_name = "Ideogram-Result-Queue"

# Static request headers. Credentials are never added here: the dict is shared
# by every call, and a Cookie left in it would leak into logs and queue payloads.
BASE_HEADERS = {
    "Origin": base_url,
    "Referer": base_url + "/",
    "DNT": "1",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "TE": "trailers",
    "User-Agent": user_agent,
}

# Resolved lazily so importing this module (and testing it) needs no AWS access.
_bucket_name: str | None = None
_ideogram_user: str | None = None
_result_queue_url: str | None = None
_auth_headers: dict | None = None
_sqs = None


class IdeogramImageError(Exception):
    """A failed Ideogram image request or session login."""


def _bucket() -> str:
    global _bucket_name
    if _bucket_name is None:
        _bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
    return _bucket_name


def _user_id() -> str:
    global _ideogram_user
    if _ideogram_user is None:
        _ideogram_user = read_ssm_param(param_name="IDEOGRAM_USER")
    return _ideogram_user


def _result_queue() -> str:
    global _sqs, _result_queue_url
    if _result_queue_url is None:
        _sqs = boto3.session.Session().client("sqs")
        _result_queue_url = _sqs.get_queue_url(QueueName=result_queue_name)["QueueUrl"]
    return _result_queue_url


def is_expired(id_token: str) -> bool:
    try:
        options = {}
        options.setdefault("verify_signature", False)
        claims = jwt.decode(jwt=id_token, options=options)
        exp = claims["exp"]
        now = datetime.now(tz=UTC).timestamp()
        logger.info("exp:%s < now:%s", exp, now)
        return exp < now
    except jwt.ExpiredSignatureError:
        logger.error("jwt expired")
        return True
    except jwt.InvalidTokenError as argument:
        logger.error("invalid token")
        logger.error(str(argument))
        return True


def refresh_iss_tokens(refresh_token: str) -> dict:
    request_ref = "https://securetoken.googleapis.com/v1/token?key=" + id_key
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Client-Version": "Firefox/JsCore/9.23.0/FirebaseCore-web",
        "User-Agent": user_agent,
        "Origin": base_url,
    }
    data = json.dumps({"grantType": "refresh_token", "refreshToken": refresh_token})
    response_object = requests.post(
        request_ref,
        headers=headers,
        data=data,
        impersonate=browser_version,
    )
    response_object_json = response_object.json()
    tokens = {
        "user_id": response_object_json["user_id"],
        "access_token": response_object_json["access_token"],
        "refresh_token": response_object_json["refresh_token"],
    }
    save_to_s3(bucket_name=_bucket(), file_name=tokens_file, value=tokens)
    return tokens


def get_session_cookies(iss_token: str) -> dict:
    request_url = f"{base_url}/api/account/login"
    login_headers = {**BASE_HEADERS, "Authorization": f"Bearer {iss_token}"}
    response_obj = requests.post(
        url=request_url,
        headers=login_headers,
        data=json.dumps({}),
        auth=("Bearer", iss_token),
    )
    if not response_obj.ok:
        logger.error(response_obj.text)
        raise IdeogramImageError(f"Error response {response_obj!s}")
    cookies = dict(response_obj.cookies)
    save_to_s3(bucket_name=_bucket(), file_name=ig_cookies, value=cookies)
    return cookies


def check_and_refresh_auth_tokens() -> dict:
    tokens = read_json_from_s3(bucket_name=_bucket(), file_name=tokens_file)
    if not tokens:
        error = f"Cannot read file '{tokens_file}' from the S3 bucket '{_bucket()}'. Put json with the field 'refresh_token' and save"
        logger.error(error)
        raise IdeogramImageError(error)
    refresh_token = tokens.get("refresh_token", None)
    if not refresh_token:
        logger.error("No 'refresh_token' found in the %s", tokens_file)
        return {}
    acc_token = tokens.get("access_token", None)
    if not acc_token or is_expired(acc_token):
        tokens = refresh_iss_tokens(refresh_token=refresh_token)
    return tokens


def authenticated_headers() -> dict:
    """Base headers plus cookies and bearer token, read from the bucket.

    Used by the result poller, which deliberately receives no credentials on its
    queue message. Cached per container: polls arrive seconds apart on the same
    warm sandbox, so the S3 reads and any token refresh happen once.
    """
    global _auth_headers
    if _auth_headers is None:
        cookie = cookie_header(
            read_json_from_s3(bucket_name=_bucket(), file_name=ig_cookies)
        )
        if not cookie:
            raise IdeogramImageError(
                f"No session cookie in '{ig_cookies}' (bucket {_bucket()}); "
                "cannot call the Ideogram API"
            )
        result = {**BASE_HEADERS, "Cookie": cookie}
        try:
            token = check_and_refresh_auth_tokens().get("access_token")
        except (BotoCoreError, ClientError, IdeogramImageError) as e:
            # The cookie alone usually suffices; a missing token must not stop
            # the poll.
            logger.warning("No access token for Ideogram API calls", exc_info=e)
            token = None
        if token:
            result["Authorization"] = f"Bearer {token}"
        _auth_headers = result
    return _auth_headers


def request_images(prompt: str) -> str:
    payload = IdeogramImageRequest(prompt=prompt, user_id=_user_id()).to_payload()
    logger.info(payload)
    tokens = check_and_refresh_auth_tokens()
    try:
        cookies = read_json_from_s3(bucket_name=_bucket(), file_name=ig_cookies)
    except (BotoCoreError, ClientError, json.JSONDecodeError, OSError):
        # Absent, unreadable or unparseable seed file: fall through to the login
        # refresh below (the file is optional and normally absent).
        logger.info("Cannot find %s in s3 bucket %s", ig_cookies, _bucket())
        cookies = None
    # ig-cookies.json is either the mapping this engine writes or a browser
    # export (list of cookie objects); both resolve through the cookie module.
    cookie = session_cookie(cookies)
    if not cookie or is_expired(cookie):
        cookies = get_session_cookies(iss_token=tokens["access_token"])
    request_headers = {
        **BASE_HEADERS,
        "Cookie": cookie_header(cookies),
        "Authorization": f"Bearer {tokens['access_token']}",
    }
    response = requests.post(
        url=post_task_url,
        headers=request_headers,
        data=json.dumps(payload),
        impersonate=browser_version,
    )
    if not response.ok:
        logger.error(response.text)
        raise IdeogramImageError(f"Error response {response!s}")
    response_body = response.json()
    logger.info(response_body)
    request_id = response_body["request_id"]
    if not request_id:
        raise IdeogramImageError(f"Error {response_body!s}")
    return request_id


def send_retrieving_event(event: dict) -> None:
    logger.info("Queueing result retrieval (result_id=%s)", event.get("result_id"))
    body = json.dumps(event)
    _sqs.send_message(QueueUrl=_result_queue(), MessageBody=body)


def __process_payload(payload: Any, request_id: str) -> None:
    prompt = payload["text"]
    if not prompt or not prompt.strip():
        return

    result_id = request_images(prompt=prompt)
    payload["result_id"] = result_id
    # No credentials on the wire: the poller reads the session cookie itself.
    payload["queue_url"] = _result_queue()
    send_retrieving_event(payload)


def sns_handler(event, context):
    """AWS SNS event handler"""
    request_id = context.aws_request_id
    logger.info("Request ID: %s", request_id)
    for record in event["Records"]:
        payload = json.loads(record["Sns"]["Message"])
        __process_payload(payload, request_id)
