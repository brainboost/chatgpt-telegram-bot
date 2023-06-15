from bing_gpt import BingGpt

text = """
[1]: https://google.com/ "Google"\n[2]: https://bing.com "Bing"\n\n\nLong text with links[^1^][1] [^2^][2].\n
"""


def test_markup_links_processed() -> None:
    bing = BingGpt(None)
    result = bing.replace_references(text)
    print(result)
    assert "^1^" not in result
