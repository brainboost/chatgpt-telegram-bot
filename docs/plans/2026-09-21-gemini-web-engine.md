# Gemini web-auth engine — Implementation Plan

> **For agentic workers:** execute task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the API-key Gemini engine with a client for the cookie-authenticated
`gemini.google.com` web chat backend, so the bot uses the same free-tier session quota
(compute-based limits refreshing every 5 hours) as the Gemini web app.

**Architecture:** Three new concerns are split out of `engines/gemini.py`:
credentials/token bootstrap (`gemini_auth.py`), the wire protocol (`gemini_web.py`), and
conversation-state persistence (an engine-scoped blob on the existing `UserContext`).
`gemini.py` shrinks to an `EngineResponder` that wires them together, so the session
runtime, failover chain and result path are untouched.

**Tech stack:** Python 3.14, `curl_cffi` (browser TLS impersonation, already a dependency),
boto3 (S3 for credentials), pytest.

**Spec / evidence:** `docs/gemini-web-protocol.md` (written in Task 8) records the
reverse-engineered wire format. Every protocol claim below was verified live on
2026-09-21 against the real account; see the Evidence table.

---

## Global Constraints

- Provider id stays `gemini`; `providers.py` is **not** modified.
- The engine must not log cookie values, the `at` token, or any credential — lengths and
  names only.
- Credentials live in the bot S3 bucket (`BOT_S3_BUCKET` SSM param), never in source.
- `payload`/`metadata`/`session uuids` are opaque identifiers, not secrets; they may be logged.
- Impersonate `chrome145` (not `chrome146+`): Chrome 146 on Windows enables Device Bound
  Session Credentials, which cookie replay cannot satisfy.
- Only `__Secure-1PSID` and `__Secure-1PSIDTS` are sent as cookies; sending the rest is
  documented to cause HTTP 401 during rotation.
- No new third-party dependency beyond what `engines/pyproject.toml` already has.
- Suite must stay green (`uv sync --all-groups` first) and `uvx ruff check` clean.

## Evidence baseline (verified 2026-09-21, live)

| Claim | Evidence |
|---|---|
| Cookie auth works, generates answers | `POST StreamGenerate` → HTTP 200, real 2341-char answer extracted |
| Minimal self-built payload works | 81-slot payload, prompt → answer `replay ok` |
| `at` token is session-scoped, reusable | HAR token still valid ~1 h later, combined with freshly scraped `bl`/`f.sid` |
| `bl`/`f.sid` reliably in `/app` HTML | `cfb2h` + `FdrFJe` found on every load |
| `SNlM0e` (`at`) only *sometimes* in `/app` | found 2/2 in one run, 0/6 in another |
| Multi-turn continuity works | follow-up into `c_aee7…` recalled the prior turn |
| `at` genuinely required | POST with no `at` → HTTP 400 |

---

## Task 1: `engines/gemini_auth.py` — credentials and bootstrap tokens

**Files:**
- Create: `engines/gemini_auth.py`
- Test: `tests/test_gemini_auth.py`

**Interfaces:**
- Produces: `GeminiCredentials(cookies: dict[str,str], access_token: str|None)`,
  `BootstrapTokens(access_token, build_label, session_id, language)`,
  `load_credentials(raw) -> GeminiCredentials`, `scrape_tokens(html) -> BootstrapTokens`,
  `CredentialsError`, `GEMINI_COOKIES_FILE`, `AUTH_COOKIE_NAMES`.

- [ ] **Step 1: failing test — credential shapes**

```python
def test_browser_export_list_keeps_only_auth_cookies():
    raw = [
        {"name": "__Secure-1PSID", "value": "abc", "domain": ".google.com"},
        {"name": "SID", "value": "nope"},
        {"name": "__Secure-1PSIDTS", "value": "def"},
    ]
    creds = load_credentials(raw)
    assert creds.cookies == {"__Secure-1PSID": "abc", "__Secure-1PSIDTS": "def"}


def test_nested_shape_carries_cached_access_token():
    raw = {"cookies": [{"name": "__Secure-1PSID", "value": "abc"}], "access_token": "tok:1"}
    creds = load_credentials(raw)
    assert creds.cookies == {"__Secure-1PSID": "abc"}
    assert creds.access_token == "tok:1"


def test_missing_psid_raises():
    with pytest.raises(CredentialsError):
        load_credentials([{"name": "SID", "value": "x"}])
```

- [ ] **Step 2: run and watch it fail** — `uv run pytest tests/test_gemini_auth.py -v`

- [ ] **Step 3: implement**, reusing the normalization idea from `ideogram_cookies.py`
  (list-of-dicts **or** `{name: value}` mapping **or** nested `{"cookies", "access_token"}`).

- [ ] **Step 4: failing test — token scrape is `None`-tolerant**

```python
def test_scrape_tolerates_missing_access_token():
    html = '<script>"cfb2h":"build_1","FdrFJe":"123"</script>'
    tokens = scrape_tokens(html)
    assert tokens.access_token is None     # SNlM0e absent: known April-2026 behaviour
    assert tokens.build_label == "build_1"
    assert tokens.session_id == "123"
    assert tokens.language == "en"         # default when TuX5cc is absent
```

- [ ] **Step 5: implement `scrape_tokens`** with the five upstream regexes verbatim:
  `"SNlM0e"`, `"cfb2h"`, `"FdrFJe"`, `"TuX5cc"`, `"qKIAYe"`.

- [ ] **Step 6: run tests, then commit** — `git commit -m "feat: Gemini web credentials and token bootstrap"`

## Task 2: `engines/gemini_web.py` — payload, request, stream parser

**Files:**
- Create: `engines/gemini_web.py`
- Test: `tests/test_gemini_web.py`

**Interfaces:**
- Consumes: `BootstrapTokens`, `GeminiCredentials` from Task 1.
- Produces: `ConversationState(cid, rid, rcid, context)` with `to_metadata()` /
  `from_metadata()`; `TurnResult(text, state, error)`; `build_payload(text, state, *, ...)`;
  `build_headers(...)`; `parse_stream(raw) -> TurnResult`; `GeminiError` subclasses
  `UsageLimitError`, `TemporarilyBlockedError`, `RequestRejectedError`; `strip_annotations(text)`.

- [ ] **Step 1: failing test — payload shape**

```python
def test_first_turn_sends_empty_metadata():
    payload = build_payload("hello", ConversationState(), language="en",
                            model_number=1, extended_thinking=True)
    inner = json.loads(json.loads(payload)[1])
    assert len(inner) == 81
    assert inner[0] == ["hello", 0, None, None, None, None, 0]
    assert inner[1] == ["en"]
    assert inner[2] == ["", "", "", None, None, None, None, None, None, ""]
    assert inner[3] is None          # deep-research token must stay unset
    assert inner[80] == 2            # extended thinking


def test_followup_sends_conversation_ids():
    state = ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx")
    inner = json.loads(json.loads(build_payload("hi", state))[1])
    assert inner[2] == ["c_1", "r_2", "rc_3", None, None, None, None, None, None, "ctx"]
```

- [ ] **Step 2: run and watch it fail**

- [ ] **Step 3: implement `build_payload`** — 81 slots; `inner[0]` message,
  `inner[1]` language, `inner[2]` metadata, `6=[1]`, `7=1`, `10=1`, `11=0`, `17=[[0]]`,
  `18=0`, `27=1`, `30=[4]`, `41=[1]`, `53=0`, `59=<request uuid>`, `61=[]`, `68=1`,
  `79=model_number`, `80=2 if extended_thinking else 1`.

- [ ] **Step 4: failing test — stream parser takes the last candidate delta**

```python
def test_parse_stream_returns_final_text_and_ids():
    raw = ")]}'\n\n38\n" + json.dumps([["wrb.fr", None, json.dumps(
        [None, ["c_1", "r_2"], None, None, [["rc_3", ["par"], None, None, None, None, None, None, [2]]]]
    )]]) + "\n"
    result = parse_stream(raw)
    assert result.text == "par"
    assert result.state.cid == "c_1"
    assert result.state.rid == "r_2"
    assert result.state.rcid == "rc_3"
```

- [ ] **Step 5: implement `parse_stream`** — iterate lines, **ignore the length prefix**
  (it is UTF-16 code units, not bytes); decode each payload as a frame array; take
  `inner[4][0][1][0]` as text, `inner[1][0]/[1]` as cid/rid, `candidate[0]` as rcid,
  `inner[25]` as context.

- [ ] **Step 6: failing test — quota exhaustion is HTTP 200 with an error frame**

```python
def test_usage_limit_error_is_detected():
    frame = ["wrb.fr", None, None, None, None, [None, None, [[None, [1037]]]]]
    raw = ")]}'\n\n" + json.dumps([frame])
    with pytest.raises(UsageLimitError):
        parse_stream(raw)
```

- [ ] **Step 7: implement error extraction** at `frame[5][2][0][1][0]` →
  `1037` usage limit, `1060` IP blocked, `1013` retryable, `frame[5][0] == 7` rejected.

- [ ] **Step 8: failing test — annotation stripping**

```python
def test_strips_followup_markup_from_accumulated_text():
    text = 'Answer.\n<FollowUp label="More?" query="Tell me more"/>'
    assert strip_annotations(text) == "Answer."
```

- [ ] **Step 9: implement `strip_annotations`** (trailing `<FollowUp …/>` plus
  `googleusercontent` artifact URLs), then commit.

## Task 3: engine-scoped session state on `UserContext`

**Files:**
- Modify: `engines/user_context.py`
- Test: `tests/test_user_context.py`

**Interfaces:**
- Produces: `UserContext.session -> dict`, `UserContext.set_session(state: dict) -> None`;
  `persist()` writes `session` alongside `turns`; `reset()` clears both.

- [ ] **Step 1: failing test**

```python
def test_session_state_roundtrips():
    store = MemoryContextStore()
    ctx = UserContext("u", "gemini", "r", store=store)
    ctx.set_session({"cid": "c_1", "rid": "r_2"})
    ctx.persist()
    assert UserContext("u", "gemini", "r", store=store).session == {"cid": "c_1", "rid": "r_2"}


def test_reset_clears_session_state():
    store = MemoryContextStore()
    ctx = UserContext("u", "gemini", "r", store=store)
    ctx.set_session({"cid": "c_1"})
    ctx.persist()
    ctx.reset()
    assert UserContext("u", "gemini", "r", store=store).session == {}
```

- [ ] **Step 2: implement**, and **delete** the now-meaningless
  `test_gemini_contents_alternate_history_then_new_text` /
  `..._without_history_sends_only_the_text` tests plus the `_build_contents` import —
  the web API takes a single message and keeps history server-side, so turn replay is gone.

- [ ] **Step 3: commit.**

## Task 4: rewrite `engines/gemini.py`

**Files:**
- Modify: `engines/gemini.py`
- Modify: `engines/pyproject.toml` (drop `google-genai`)
- Test: `tests/test_gemini.py`

**Interfaces:**
- Consumes everything above. `GeminiResponder.answer(payload, context)` reads
  `context.session`, calls the client, writes the new state back, returns text.

- [ ] **Step 1: failing test** — responder round-trip with a stubbed client:

```python
def test_responder_persists_conversation_state(monkeypatch):
    responder = GeminiResponder()
    monkeypatch.setattr(gemini, "_generate", lambda text, state: TurnResult(
        text="hi", state=ConversationState(cid="c_1", rid="r_2", rcid="rc_3", context="ctx")))
    ctx = UserContext("u", "gemini", "r", store=MemoryContextStore())
    assert responder.answer({"text": "q"}, ctx) == "hi"
    assert ctx.session["cid"] == "c_1"
```

- [ ] **Step 2: implement the responder**, keeping `label = "gemini"`,
  `wants_session = True`, `fails_over = True`.

- [ ] **Step 3: implement the credential-loading client** — read
  `gemini-cookies.json` from S3, scrape tokens from `GET /app`, fall back to the cached
  `access_token`, and raise a loud, actionable error when neither a token nor a
  `__Secure-1PSID` is available.

- [ ] **Step 4: run the suite and ruff, then commit.**

## Task 5: live verification

- [ ] Run the engine against the real bucket with a throwaway prompt via a scratch script
      outside the repo; confirm a real answer and that a second call continues the thread.
- [ ] Confirm no credential appears in any log line.

## Task 6: docs

- [ ] `docs/gemini-web-protocol.md` — the reverse-engineered protocol: endpoints, token
      names, payload indices, frame format, error codes, and a "how to recapture" section
      for when Google changes it.
- [ ] `CONTEXT.md` — add the "provider session" term.
- [ ] Seed instructions for `gemini-cookies.json` (browser export → bucket).
- [ ] Note the risky assumptions: `at` scrape flakiness, volatile model hashes, DBSC.

## Task 7: PR

- [ ] Commit series pushed from the `gemini` worktree, PR against `main` with the evidence
      table, the deploy note (engines bundle only — no infra change) and a rollback note.

---

## Deliberately out of scope

- Dynamic model discovery via the `otAQ7b` RPC (hardcode `fbb127bbb056c959`, document it).
- `__Secure-1PSIDTS` rotation (`POST accounts.google.com/RotateCookies`) — a follow-up;
  the stored export is treated as operator-provided and long-lived.
- Deep Research, temporary chats, Gems, file uploads, image generation.
- Token/cookie refresh from Telegram (an admin command).
