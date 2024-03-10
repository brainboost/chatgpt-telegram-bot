from engines.claude import ask, process_attachments


class MockContext:
    def __init__(self):
        self.conversation_id = ""


def test_ask(capsys):
    resp = ask(
        context=MockContext(),
        text="tell me a joke",
        attachments=None,
    )
    with capsys.disabled():
        print(resp)


def test_process_attachments_none(capsys):
    resp = process_attachments(attachments=None)
    with capsys.disabled():
        print(resp)
