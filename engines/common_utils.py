import base64
import json
import logging
import re
import zlib
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")
esc_pattern = re.compile(f"(?<!\\|)([{re.escape(r'.-+#|{}!=()<>')}])(?!\\|)")


def read_ssm_param(param_name: str) -> str:
    ssm_client = boto3.client(service_name="ssm")
    return ssm_client.get_parameter(Name=param_name)["Parameter"]["Value"]


def read_json_from_s3(bucket_name: str, file_name: str) -> Any:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket_name, Key=file_name)
    file_content = response["Body"].read().decode("utf-8")
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


def escape_markdown_v2(text: str) -> str:
    return re.sub(pattern=esc_pattern, repl=r"\\\1", string=text)


def get_image(s3_uri: str | None, bucket_name: str) -> Optional[str]:
    if not s3_uri:
        return None
    file_name = urlparse(s3_uri).path.split("/")[-1]
    logging.info(f"Downloading file 'att/{file_name}' from s3 bucket {bucket_name}")
    tmp_file = f"/tmp/{file_name}"
    session = boto3.session.Session()
    session.client("s3").download_file(
        Bucket=bucket_name,
        Key=f"att/{file_name}",
        Filename=tmp_file,
    )
    if not (img := Path(tmp_file)).exists():
        logging.error(
            f"File {tmp_file} does not exist. Problem to download from s3 '{s3_uri}'"
        )
        raise FileNotFoundError(f"Could not find image: {img}")
    return tmp_file
