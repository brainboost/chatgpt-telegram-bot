import pytest

from engines.common_utils import read_json_from_s3, read_ssm_param
from engines.ideogram_img import (
    check_and_refresh_google_token,
    is_expired,
    refresh_tokens,
    request_images,
)


@pytest.mark.skip()
def test_check_and_refresh(capsys):
    with capsys.disabled():
        check_and_refresh_google_token()


@pytest.mark.skip()
def test_refresh(capsys):
    with capsys.disabled():
        bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
        token = read_json_from_s3(bucket_name=bucket_name, file_name="google_auth.json")
        data = refresh_tokens(token["refresh_token"])
        assert not is_expired(data["id_token"])


@pytest.mark.skip()
def test_authorize(capsys):
    with capsys.disabled():
        response = request_images(prompt="cute kittens playing with yarn ball")
        print(response)
