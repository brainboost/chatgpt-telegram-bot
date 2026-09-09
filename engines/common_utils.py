import base64
import json
import logging
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)


def read_ssm_param(param_name: str) -> str:
    ssm_client = boto3.client(service_name="ssm")
    return ssm_client.get_parameter(Name=param_name)["Parameter"]["Value"]


def write_ssm_param(param_name: str, value: str) -> None:
    ssm_client = boto3.client(service_name="ssm")
    ssm_client.put_parameter(Name=param_name, Value=value, Type="String", Overwrite=True)


def read_json_from_s3(bucket_name: str, file_name: str) -> Any | None:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket_name, Key=file_name)
    body = response.get("Body", None)
    if not body:
        return None
    file_content = body.read().decode("utf-8")
    return json.loads(file_content)


def json_cookies_to_header_string(cookies_json: Any):
    cookie_pairs = []
    for cookie_data in cookies_json:
        cookie_pairs.append(f"{cookie_data['name']}={cookie_data['value']}")
    return "; ".join(cookie_pairs)


def save_to_s3(bucket_name: str, file_name: str, value: Any) -> None:
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket_name, Key=file_name, Body=json.dumps(value))


def encode_message(text: str) -> str:
    zipped = zlib.compress(text.encode("utf-8"))
    return base64.b64encode(zipped).decode("ascii")


def get_s3_file(s3_uri: str | None, bucket_name: str) -> str | None:
    if not s3_uri:
        return None
    file_name = urlparse(s3_uri).path.split("/")[-1]
    logger.info("Downloading file 'att/%s' from s3 bucket %s", file_name, bucket_name)
    tmp_file = f"/tmp/{file_name}"
    session = boto3.Session()
    session.client("s3").download_file(
        Bucket=bucket_name,
        Key=f"att/{file_name}",
        Filename=tmp_file,
    )
    if not (img := Path(tmp_file)).exists():
        logger.error("File %s does not exist. Problem to download from s3 '%s'", tmp_file, s3_uri)
        raise FileNotFoundError(f"Could not find image: {img}")
    return tmp_file
