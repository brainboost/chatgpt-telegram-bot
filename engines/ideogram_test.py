from .ideogram_img import request_images


def test_auth_token_correct():
    result = request_images("a tree frog smiling with its mouth open")
    assert result is not None
