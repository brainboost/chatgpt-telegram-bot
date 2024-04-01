import pytest

from engines.dalle_img import imageGen


@pytest.mark.skip()
def test_check_and_refresh(capsys):
    with capsys.disabled():
        prompt = "roasted coffee beans in palms"
        assert imageGen
        list = imageGen.get_images(prompt)
        assert list
        print(list)
