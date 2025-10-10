from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from engines.claude import ask, process_attachments
from engines.user_context import UserContext


class MockContext(UserContext):
    def __init__(self, user_id: str = "test_user", engine_id: str = "claude", request_id: str = "test_request", username: str = "test_user"):
        # Initialize with required parameters but use mocks for AWS resources
        self.context_table = MagicMock()
        self.conversations_table = MagicMock()
        self.context = None
        self.conversation_id = ""
        self.parent_id = None

        # Set attributes that UserContext.__init__ would set
        self.user_id = user_id
        self.username = username or "anonymous"
        self.engine_id = engine_id
        self.request_id = request_id

    def reset_conversation(self) -> None:
        self.conversation_id = None

    def read_context(self) -> Optional[Any]:
        return None

    def save_context(self, optional_context: Optional[Any] = None) -> None:
        pass

    def save_conversation(self, conversation: Optional[Any] = None) -> None:
        pass

    def read_conversation(self) -> Optional[Any]:
        return None


@pytest.mark.skip()
def test_ask(capsys):
    resp = ask(
        context=MockContext(),
        text="tell me a joke",
        attachments=None,
    )
    with capsys.disabled():
        print(resp)


@pytest.mark.skip()
def test_process_attachments_none(capsys):
    resp = process_attachments(attachments="")
    with capsys.disabled():
        print(resp)
