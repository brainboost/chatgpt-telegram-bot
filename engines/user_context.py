"""Per-user conversation memory for one engine.

The deepened conversation-state module (candidate 3): one row per user x engine
in the ``user-context`` table holds the recent exchanges, bounded and
self-cleaning via TTL. The contract is real: ``/reset`` deletes the row, chat
engines read the loaded turns, and nothing grows without bound.

Interface summary (everything a caller must know):

- ``ContextStore`` is a raw I/O seam with two adapters: ``DynamoContextStore``
  (production, lazy boto3) and ``MemoryContextStore`` (tests). All row shape,
  key format, turn capping and expiry live in :class:`UserContext`, never in
  the adapters.
- ``UserContext(user_id, engine_id, request_id, username=None, store=None)``
  loads the stored turns at construction. ``store`` defaults to the DynamoDB
  adapter, so construction stays import-safe (boto3 is only touched on the
  first store call).
- After an answer: ``add_turn(request, response)`` then ``persist()``. Each
  side is trimmed to ``TURN_CHAR_CAP`` characters and at most ``MAX_TURNS``
  exchanges are kept (oldest dropped). ``persist()`` writes the row with a
  60-day ``exp`` TTL. ``reset()`` deletes the row and clears memory.

Engine-scoped session state travels in the same row. ``session`` is an opaque
``dict`` the engine owns and interprets; the context module neither reads nor
validates it. It exists because some providers keep conversation history on
their own side (the Gemini web backend identifies a thread by id) instead of
accepting a replayed transcript, so the identifiers have to be persisted
somewhere with the same lifetime and reset semantics as ``turns``.

Deliberate shape change: rows are now ``{user_id, engine, turns, session, exp}``.
The old ``conversation_id``/``parent_id``/``optional`` columns are no longer
written; a legacy row without ``turns`` simply reads as an empty memory and is
overwritten on the next ``persist()``.
"""

import logging
import time
from typing import Protocol

import boto3

logging.basicConfig()
logging.getLogger().setLevel("INFO")
logger = logging.getLogger(__name__)

CONTEXT_TABLE = "user-context"
MAX_TURNS = 8  # exchanges kept per user x engine
TURN_CHAR_CAP = 4000  # per request/response side, keeps the row well under 400 KB
TTL_DAYS = 60


class ContextStore(Protocol):
    """Raw persistence for one user x engine context row."""

    def load(self, user_id: str, engine_id: str) -> dict | None:
        """Return the stored row, or None when the user has no memory yet."""

    def save(self, user_id: str, engine_id: str, item: dict) -> None:
        """Store the row (item carries user_id/engine/turns/exp)."""

    def delete(self, user_id: str, engine_id: str) -> None:
        """Delete the row for this user x engine."""


class DynamoContextStore:
    """Production adapter: the ``user-context`` table, lazily bound."""

    def __init__(self) -> None:
        self._table = None

    def _client(self):
        if self._table is None:
            self._table = boto3.resource("dynamodb").Table(CONTEXT_TABLE)
        return self._table

    def load(self, user_id: str, engine_id: str) -> dict | None:
        try:
            resp = self._client().get_item(
                Key={"user_id": user_id, "engine": engine_id}
            )
            return resp.get("Item")
        except Exception as e:  # a read failure must not block the answer
            logger.error(
                "Cannot read context for user %s engine %s", user_id, engine_id,
                exc_info=e,
            )
            return None

    def save(self, user_id: str, engine_id: str, item: dict) -> None:
        try:
            self._client().put_item(
                Item={"user_id": user_id, "engine": engine_id, **item}
            )
        except Exception as e:
            logger.error(
                "Cannot save context for user %s engine %s", user_id, engine_id,
                exc_info=e,
            )

    def delete(self, user_id: str, engine_id: str) -> None:
        try:
            self._client().delete_item(
                Key={"user_id": user_id, "engine": engine_id}
            )
        except Exception as e:
            logger.error(
                "Cannot delete context for user %s engine %s", user_id, engine_id,
                exc_info=e,
            )


class MemoryContextStore:
    """Test adapter: keeps rows in a dict, no AWS."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    def load(self, user_id: str, engine_id: str) -> dict | None:
        return self.rows.get((user_id, engine_id))

    def save(self, user_id: str, engine_id: str, item: dict) -> None:
        self.rows[(user_id, engine_id)] = {"user_id": user_id, "engine": engine_id, **item}

    def delete(self, user_id: str, engine_id: str) -> None:
        self.rows.pop((user_id, engine_id), None)


class UserContext:
    """One engine's conversation memory for one user (composite user key)."""

    def __init__(
        self,
        user_id: str,
        engine_id: str,
        request_id: str,
        username: str | None = None,
        store: ContextStore | None = None,
    ) -> None:
        self.user_id = user_id
        self.engine_id = engine_id
        self.request_id = request_id
        self.username = username or "anonymous"
        self._store: ContextStore = store if store is not None else DynamoContextStore()
        item = self._store.load(user_id, engine_id)
        self._turns: list = item.get("turns", []) if item else []
        stored_session = item.get("session") if item else None
        self._session: dict = stored_session if isinstance(stored_session, dict) else {}

    @property
    def turns(self) -> list:
        """Loaded exchanges, oldest first: [{request, response}, ...]."""
        return self._turns

    @property
    def session(self) -> dict:
        """Engine-owned continuation state (opaque to this module)."""
        return self._session

    def set_session(self, state: dict) -> None:
        """Replace the engine-owned continuation state."""
        self._session = dict(state)

    def add_turn(self, request: str, response: str) -> None:
        self._turns.append(
            {
                "request": request[:TURN_CHAR_CAP],
                "response": response[:TURN_CHAR_CAP],
            }
        )
        if len(self._turns) > MAX_TURNS:
            self._turns = self._turns[-MAX_TURNS:]

    def persist(self) -> None:
        row = {
            "turns": self._turns,
            "exp": int(time.time()) + TTL_DAYS * 24 * 3600,
        }
        if self._session:
            row["session"] = self._session
        self._store.save(self.user_id, self.engine_id, row)

    def reset(self) -> None:
        """Forget this engine's memory for the user."""
        self._store.delete(self.user_id, self.engine_id)
        self._turns = []
        self._session = {}
