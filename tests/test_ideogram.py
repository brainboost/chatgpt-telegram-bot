import pytest

from engines.common_utils import read_json_from_s3, read_ssm_param
from engines.ideogram_img import (
    check_and_refresh_auth_tokens,
    get_session_cookies,
    is_expired,
    refresh_iss_tokens,
    request_images,
)


@pytest.mark.skip()
def test_check_and_refresh(capsys):
    with capsys.disabled():
        tokens = check_and_refresh_auth_tokens()
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        print(tokens)


@pytest.mark.skip()
def test_refresh(capsys):
    with capsys.disabled():
        bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
        token = read_json_from_s3(bucket_name=bucket_name, file_name="google_auth.json")
        data = refresh_iss_tokens(token["refresh_token"])
        assert not is_expired(data["access_token"])


# @pytest.mark.skip()
def test_request_images(capsys):
    with capsys.disabled():
        response = request_images(
            prompt="cute kittens playfully engaging with a colorful yarn ball"
        )
        assert response


@pytest.mark.skip()
def test_get_session_cookies(capsys):
    with capsys.disabled():
        bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
        token = read_json_from_s3(bucket_name=bucket_name, file_name="google_auth.json")
        response = get_session_cookies(iss_token=token["access_token"])
        assert response


@pytest.mark.skip()
def test_is_expired(capsys):
    with capsys.disabled():
        bucket_name = read_ssm_param(param_name="BOT_S3_BUCKET")
        token = read_json_from_s3(bucket_name=bucket_name, file_name="google_auth.json")
        assert not is_expired(token["access_token"])
