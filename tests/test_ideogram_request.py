"""Tests for the typed Ideogram image request (no AWS, no network).

The expected default payload is the JSON ideogram.ai itself sent to
``/api/images/sample`` (captured 2026-09), so the wire contract is pinned to a
real capture rather than to whatever the engine happens to build.
"""

import importlib

request_mod = importlib.import_module("engines.ideogram_request")
IdeogramImageRequest = request_mod.IdeogramImageRequest
Resolution = request_mod.Resolution
InvalidImageRequest = request_mod.InvalidImageRequest

CAPTURED_PAYLOAD = {
    "prompt": "San Francisco bay in golden hour from the above",
    "user_id": "test-user",
    "model_version": "AUTO",
    "model_uri": "model/AUTO/version/0",
    "use_autoprompt_option": "AUTO",
    "sampling_speed": 2,
    "character_reference_parents": [],
    "product_reference_parents": [],
    "resolution": {"width": 1280, "height": 800},
    "num_images": 4,
    "style_type": "AUTO",
}

# The capture asked for 1280x800 landscape; the engine deliberately sends a
# square 1024x1024 instead. Everything else matches the capture.
EXPECTED_DEFAULT_PAYLOAD = {
    **CAPTURED_PAYLOAD,
    "resolution": {"width": 1024, "height": 1024},
}


def test_defaults_match_the_captured_payload_but_stay_square():
    request = IdeogramImageRequest(
        prompt=CAPTURED_PAYLOAD["prompt"], user_id=CAPTURED_PAYLOAD["user_id"]
    )

    assert request.to_payload() == EXPECTED_DEFAULT_PAYLOAD


def test_model_uri_follows_the_model_version():
    request = IdeogramImageRequest(
        prompt="a cat", user_id="u", model_version="V_1_5"
    )

    assert request.to_payload()["model_uri"] == "model/V_1_5/version/0"


def test_explicit_model_uri_is_kept():
    request = IdeogramImageRequest(
        prompt="a cat", user_id="u", model_uri="model/custom/version/7"
    )

    assert request.to_payload()["model_uri"] == "model/custom/version/7"


def test_values_can_be_overridden():
    request = IdeogramImageRequest(
        prompt="a cat",
        user_id="u",
        num_images=1,
        sampling_speed=0,
        style_type="REALISTIC",
        resolution=Resolution(width=1024, height=1024),
    )

    payload = request.to_payload()
    assert payload["num_images"] == 1
    assert payload["sampling_speed"] == 0
    assert payload["style_type"] == "REALISTIC"
    assert payload["resolution"] == {"width": 1024, "height": 1024}


def test_each_payload_is_independent():
    first = IdeogramImageRequest(prompt="a cat", user_id="u")
    second = IdeogramImageRequest(prompt="a dog", user_id="u")

    first_payload = first.to_payload()
    first_payload["character_reference_parents"].append("reference-1")

    assert first.character_reference_parents == []  # frozen instance untouched
    assert second.to_payload()["character_reference_parents"] == []


def test_reference_parents_are_passed_through():
    request = IdeogramImageRequest(
        prompt="a cat",
        user_id="u",
        character_reference_parents=["c1"],
        product_reference_parents=["p1"],
    )

    payload = request.to_payload()
    assert payload["character_reference_parents"] == ["c1"]
    assert payload["product_reference_parents"] == ["p1"]


def test_empty_prompt_is_rejected():
    for prompt in ("", "   ", "\n"):
        try:
            IdeogramImageRequest(prompt=prompt, user_id="u")
        except InvalidImageRequest:
            continue
        raise AssertionError(f"prompt {prompt!r} should have been rejected")


def test_empty_user_id_is_rejected():
    try:
        IdeogramImageRequest(prompt="a cat", user_id="  ")
    except InvalidImageRequest:
        return
    raise AssertionError("blank user_id should have been rejected")


def test_num_images_is_bounded():
    for count in (0, -1, request_mod.MAX_NUM_IMAGES + 1):
        try:
            IdeogramImageRequest(prompt="a cat", user_id="u", num_images=count)
        except InvalidImageRequest:
            continue
        raise AssertionError(f"num_images={count} should have been rejected")


def test_sampling_speed_is_bounded():
    for speed in (-1, request_mod.MAX_SAMPLING_SPEED + 1):
        try:
            IdeogramImageRequest(prompt="a cat", user_id="u", sampling_speed=speed)
        except InvalidImageRequest:
            continue
        raise AssertionError(f"sampling_speed={speed} should have been rejected")


def test_resolution_must_be_positive():
    try:
        Resolution(width=0, height=100)
    except InvalidImageRequest:
        return
    raise AssertionError("zero width should have been rejected")


def test_blank_model_fields_are_rejected():
    for kwargs in (
        {"model_version": " "},
        {"use_autoprompt_option": ""},
        {"style_type": ""},
    ):
        try:
            IdeogramImageRequest(prompt="a cat", user_id="u", **kwargs)
        except InvalidImageRequest:
            continue
        raise AssertionError(f"{kwargs} should have been rejected")
