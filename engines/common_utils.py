import base64
import json
import logging
import re
import zlib
from pathlib import Path
from typing import Any
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


def save_to_s3(bucket_name: str, file_name: str, value: Any) -> None:
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket_name, Key=file_name, Body=json.dumps(value))


def encode_message(text: str) -> str:
    zipped = zlib.compress(text.encode("utf-8"))
    return base64.b64encode(zipped).decode("ascii")


def escape_markdown_v2(text: str) -> str:
    return re.sub(pattern=esc_pattern, repl=r"\\\1", string=text)


def get_image(s3_uri: str, bucket_name: str) -> str:
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


# def compress_image(infile: bytes) -> str:
#     img = Image.open(BytesIO(infile))
#     img = img.convert("RGB")
#     size = len(infile)
#     max_size = 1e6
#     if size <= max_size:
#         outfile = BytesIO()
#         img.save(outfile, format="JPEG", quality=80, optimize=True)
#         return base64.b64encode(outfile.getvalue()).decode("utf-8")
#     else:
#         ratio = (size / max_size) ** 0.6
#         new_width = int(img.width / ratio)
#         new_height = int(img.height / ratio)
#         img = ImageOps.fit(img, (new_width, new_height), method=Image.LANCZOS)
#         outfile = BytesIO()
#         img.save(outfile, format="JPEG", quality=80, optimize=True)
#         return base64.b64encode(outfile.getvalue()).decode("utf-8")
