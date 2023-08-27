import base64
import json
import logging
import zlib
import re
import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")
esc_pattern = re.compile(f"(?<!\|)([{re.escape(r'.-+#|{}!=()<>')}])(?!\|)")


def read_ssm_param(param_name: str) -> str:
    ssm_client = boto3.client(service_name="ssm")
    return ssm_client.get_parameter(Name=param_name)["Parameter"]["Value"]


def read_json_from_s3(bucket_name: str, file_name: str) -> dict:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket_name, Key=file_name)
    file_content = response["Body"].read().decode("utf-8")
    return json.loads(file_content)


def encode_message(text: str) -> str:
    zipped = zlib.compress(text.encode("utf-8"))
    return base64.b64encode(zipped).decode("ascii")


def escape_markdown_v2(text: str) -> str:
    return re.sub(pattern=esc_pattern, repl=r"\\\1", string=text)
