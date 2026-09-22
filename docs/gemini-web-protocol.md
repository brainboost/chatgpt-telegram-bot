# Gemini web protocol reference

The `gemini` provider talks to the **Gemini web app backend** at
`gemini.google.com`, authenticated by replaying a logged-in browser's cookies.
This is not a public API: everything below was reverse-engineered from a captured
HAR and then confirmed by live replay. Google changes it without notice, so this
document exists as much for repair as for explanation.

**Last verified:** 2026-09-21, against a live free-tier account.

## Why this backend

The web app's allowance is compute-based and refreshes every five hours, separate
from the API key's own quota. That is the whole point of the change: the previous
implementation used `google-genai` with an API key, and hit that key's limits.

The trade is that history is not ours to replay — see "Conversation state".

## Request

```
POST https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate
    ?bl=<build label>&f.sid=<session id>&hl=<lang>&_reqid=<n>&rt=c
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Origin: https://gemini.google.com
Referer: https://gemini.google.com/
X-Same-Domain: 1
x-goog-ext-525001261-jspb: <model header>
x-goog-ext-525005358-jspb: ["<request uuid>",1]
x-goog-ext-73010989-jspb: [0]
x-goog-ext-73010990-jspb: [0,0,0]

at=<access token>&f.req=<urlencoded json>
```

`bl` and `f.sid` are omitted (not empty) when unknown. `_reqid` starts at a random
five-digit number and grows by 100000 per request.

`x-goog-ext-525005358-jspb` must carry the *same* uuid that payload slot 59 holds.
The two `73010989`/`73010990` headers are opaque constants that never vary; send
them verbatim.

## Bootstrap tokens

`GET https://gemini.google.com/app` and scrape the page globals with plain
substring searches — no JSON or JS parsing layer:

| Regex | Meaning | Required? |
|---|---|---|
| `"SNlM0e":\s*"(.*?)"` | `at` access/anti-CSRF token | **yes** — a request without it is HTTP 400 |
| `"cfb2h":\s*"(.*?)"` | `bl` server build label | optional, but rolling weekly |
| `"FdrFJe":\s*"(.*?)"` | `f.sid` session id | optional, per page load |
| `"TuX5cc":\s*"(.*?)"` | locale — **normalize `en-US` → `en`** | optional |
| `"qKIAYe":\s*"(.*?)"` | upload Push-ID (unused here) | optional |

**The `at` token is the fragile part.** Since roughly April 2026 Google only
sometimes embeds `SNlM0e` in the `/app` HTML; a run of six consecutive loads
returned it zero times, while an earlier run returned it twice. Measured
properties:

- it is **session-scoped, not page-scoped** — a token captured from one page load
  still worked roughly an hour later, combined with a *freshly* scraped `bl` and
  `f.sid`;
- it is **required** — the same request without it returns HTTP 400.

So the engine scrapes, falls back to the cached `gemini-token.json`, and writes the
cache back whenever a scrape does succeed. `gemini-cookies.json` is operator input
and is never rewritten; the cache is a separate object so refreshing a token can
never damage the exported session.

## Payload (`f.req`)

`f.req` is `[null, "<inner json string>"]`. The inner value is a fixed **81-slot**
list:

| Slot | Value |
|---|---|
| 0 | `[prompt, 0, None, None, None, None, 0]` |
| 1 | `[language]` |
| 2 | conversation metadata, 10 slots — see below |
| **3** | Deep Research session token. **Must stay `None` for normal chat** |
| 4 | Deep Research uuid (`uuid4().hex`) — likewise unset here |
| 6 | `[1]` |
| 7 | `1` (streaming) |
| 10, 11, 18, 27, 53, 68 | `1`, `0`, `0`, `1`, `0`, `1` |
| 17 | `[[0]]` |
| 30 | `[4]` |
| 41 | `[1]` |
| 59 | per-request uuid, mirrored into the `525005358` header |
| 61 | `[]` |
| 79 | model number (`1` Flash, `3` Pro, `6` Lite) |
| 80 | `2` extended thinking, `1` standard |

A captured request had slot 3 populated with a ~2.7 KB `!…` string and slot 4 with
a `uuid4().hex` — the exact signature of Deep Research, which is why slots 3/4 are
left unset deliberately.

### Conversation state (multi-turn)

Slot 2 is `[cid, rid, rcid, None ×6, context]`. The **only** difference between a
first turn and a follow-up is this slot, because slot 0 holds a single message and
Google keeps the transcript:

- first turn: `["", "", "", None …×6, ""]`
- follow-up: `[<conversation id>, <response id>, <candidate id>, None ×6, <context>]`

`cid`/`rid` come from the previous response's `inner[1][0]`/`[1][1]`, `rcid` from
the answer candidate's `[0]`. Verified live: a follow-up that supplied these
correctly recalled the previous turn, and one sent with an **empty** context slot
also worked, so `context` is a fidelity bonus rather than a requirement.

## Model header

`x-goog-ext-525001261-jspb` is a 17-element array:

| Index | Meaning |
|---|---|
| 4 | model id hash |
| 7 | temporary-chat flag |
| 8 | client capabilities (`[4,5,6,8]`) |
| 11 | tier capacity: **1 = free**, 2 advanced, 3 pro, 4 plus |
| 14 | model number |
| 15 | thinking level: 1 standard, 2 extended |
| 16 | per-session uuid (`uuid4().upper()`) |

**Model hashes are volatile.** `fbb127bbb056c959` is the free-tier Flash id at the
time of writing; Google rotates these, and a stale id surfaces as error 1050/1052.
The current ids can also be read out of the live frontend bundle
(`.../_/mss/boq-bard-web/_/js/...`), which listed
`56fdd199312815e2`, `fbb127bbb056c959`, `a74ec8485b3b5ce4`, `1bc6b5d98741cd3d`.

## Response

```
)]}'

177
[["wrb.fr",null,"[null,[\"c_…\",\"r_…\"],{…}]"]]
253
…
```

Frames are `["wrb.fr", rpcid, "<inner json string>", …]`; `rpcid` is `null` for
StreamGenerate, and the third element is JSON that must be decoded a second time.

**Ignore the length marker.** Google counts it in UTF-16 code units, not bytes, so
trusting it desynchronises on any answer containing an emoji or a rare CJK
character. JSON never contains a raw newline, so a line is always a whole value —
parse line-wise and skip anything that is not a JSON array.

| Path | Meaning |
|---|---|
| `inner[1][0]`, `inner[1][1]` | conversation id `c_…`, response id `r_…` |
| `inner[4][0][0]` | candidate id `rc_…` |
| `inner[4][0][1][0]` | **the answer text** (arrives as growing deltas) |
| `inner[4][0][8][0]` | `1` in progress, `2` complete |
| `inner[25]` | conversation context token |

JSPB delivers high-numbered fields sparsely, in a dict keyed by **field number + 1**,
so field 25 can also arrive as metadata key `"26"` rather than positionally. The
engine reads only the positional slot: continuity works with an empty context
(verified live), so chasing the sparse form buys fidelity a Telegram bot cannot use.

Other frame tags — `di`, `af.httprm`, `e` — are bookkeeping. They also appear in
successful streams, so a tag alone means nothing; the trailing number on
`af.httprm` is a running byte count, not an error code.

### Errors arrive inside an HTTP 200

Quota and abuse conditions are **not** HTTP status codes. The code sits at
`frame[5][2][0][1][0]`, wrapped as
`["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",[<code>]]`,
and a rejected request instead puts `7` in `frame[5][0]`.

| Code | Meaning | Engine behaviour |
|---|---|---|
| 1037 | free-tier usage limit for the window | `UsageLimitError` → failover |
| 1060 | IP temporarily blocked | `TemporarilyBlockedError` → failover |
| 7 | request rejected | `RequestRejectedError` — usually expired cookies |
| 1096 | appended to **every** turn observed, successful or not | logged as a warning, then ignored |
| 1097 | any turn carrying conversation ids, no answer at all — a per-account throttle on multi-turn chats (see below) | `ConversationNotContinuableError` → thread dropped, retried as a new conversation |

### A code can arrive *after* a good answer

This one cost real debugging time. A captured stream contained, in order: the
finished answer with completion marker `2`, a generated conversation title, **and
then** an error frame:

```
…[["wrb.fr",null,"[null,[\"c_…\",\"r_…\"],null,null,[[\"rc_…\",[\"probe ok\"],…,[2],…]]]"]]
…[["wrb.fr",null,"[null,[\"c_…\",\"r_…\"],{\"11\":[\"System Probe Confirmation\"],\"44\":true}]"]]
…[["wrb.fr",null,null,null,null,[13,null,[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",[1096]]]]]]
```

So an error code is **not** by itself a failed turn. The engine therefore treats a
code as fatal only when the stream produced no answer; a code that arrives
alongside a complete answer is logged and ignored. Raising on it (the first
implementation) discarded a finished reply and failed the request over to another
provider for no reason.

Codes are also not all equally meaningful: an unknown code is logged at error
level so a persistent one is visible in CloudWatch rather than only showing up as
the failover chain quietly taking over. When quota is spent the stream can
additionally carry only bookkeeping and no `wrb.fr` payload at all (one such
response was 218 bytes); that surfaces as `StreamAbortedError`.

### The completion marker decides whether an answer may be delivered

Google streams the answer as it is generated, so the frames already received hold
a partial reply whenever the connection stops early — a stall, a dropped socket,
or Google abandoning a turn it is throttling. Measured live: one request received
3586 bytes and then nothing for 180 s before curling out.

The last candidate frame of a finished turn carries `inner[4][0][8][0] == 2`,
where every earlier frame carries `1`. That held for every complete turn captured
while writing this (answers of 1107 and 11 283 characters among them), so the
engine returns an answer only once the marker has been seen. Without it the turn
fails with `StreamAbortedError`, or with the in-stream code when one explains the
cut — a quota code is account-wide, so it must not be recorded as the thread's
fault.

This is a deliberate change of behaviour. An earlier version returned whatever
text it had with only a log line, which is how a reply that stops mid-sentence
reaches the user with nothing to account for it. Failing instead lets the failover
chain, or `_run_turn`'s fresh-conversation retry, answer properly.

**1097 is a per-account throttle on multi-turn chats (resolved 2026-09-22).** Every
turn that carries conversation ids is refused with a bare 1097 and no answer, while
every first turn succeeds — so an affected account loses all memory between
messages, and each follow-up logs one line and is retried as a new conversation.

The payload was never the problem. Each explanation below was tested and **ruled
out**, and the code path they all exercise was then shown working end to end from a
workstation with a freshly exported browser session from a **different** account
(no cached `at`):

| Probe, different account | Result |
|---|---|
| first turn, "Remember the number 4817" | answered |
| follow-up, same client | answered `4817` |
| follow-up to the same thread, **new client** (fresh `session_uuid`) | answered `4817` |

Three conclusions follow from that run, and each one closes a question the entries
below had left open:

- **The throttle belongs to the account**, so the remedy is fresh cookies from
  another account in `gemini-cookies.json` — a browser cookie export is accepted
  as-is, list shape included — not a code change.
- **The per-client `session_uuid` does not tie a thread to the container that
  created it**, which matters because a follow-up may land on any Lambda.
- **No continuation token is needed at all**: metadata slot 25 is empty on every
  turn of that working chain, so `[cid, rid, rcid, …]` is sufficient and there is
  no token to fetch from another RPC. That also explains why a successful turn's
  response contains none — scanning every frame of a live first turn for any string
  longer than 40 characters finds nothing, the final frame's inner payload has only
  three slots (`1` = ids, `2` = the generated title), and slot 25 is empty.

| Hypothesis | Test | Result |
|---|---|---|
| `at`/`bl`/`f.sid` triple is not self-consistent | used the capture's own consistent triple | still 1097 |
| Server rotated `__Secure-1PSIDTS` and we ignored it | inspected `Set-Cookie` on both turns | only an unrelated `__Secure-ENID`; no rotation |
| `rcid` missing or stale | sent the candidate id, and sent none | 1097 either way |
| Continuation token needed at metadata slot 9 | sent it when available, and empty | 1097 either way; and a working chain has it empty |
| Payload shape wrong | identical shape succeeded earlier the same day | not the shape |
| Conversations created by our own replay are not continuable | followed up into a conversation the **browser** created (taken from the capture) | still 1097 |
| A different bootstrap route exposes `at` more reliably | 12 loads across 6 routes (`/app`, `/app?hl=en`, `/`, `/u/0/app`, `/u/0/`, `?authuser=0`) | 0/12; the token's presence is time-dependent, not path-dependent |
| The browser's other payload differences matter | matched its slot 41 = `[2]` and slot 68 = `2`, and its 99-slot array length | 1097 either way; first turns unaffected |
| `source-path=/app/<cid>` is required | added it to the query string | still 1097 |
| The capability list must be doubled | sent `[4,5,6,8,4,5,6,8]` | still 1097 |
| Metadata must use empty strings rather than nulls | sent `["c…","r…","rc…","","","","","","",""]` | HTTP **400**, so slot 2 is validated — but with real ids and nulls it is 1097 |

Note that `at` **was** scraped successfully on that other account's first page load,
so the scrape is opportunistic per session, not broken: when the page omits the
token and no cache exists, an engine cold start has nothing to send and must fail
over. The route sweep means there is no better bootstrap URL to switch to.

One debugging trap is worth recording: an earlier verification harness injected the
captured token whenever the scrape missed, so every follow-up attempt silently used
a stale token while the bootstrap log still said `at=present`. Check the value
actually reaching `build_generate_request`, not the log line.

Because a refusal is an expected answer rather than a defect, `_run_turn` reports it
in one line and without a traceback; a traceback per message would bury the failures
that are genuinely unexpected. Expect a replacement account to be throttled the same
way under sustained multi-turn load — the failure mode is safe, since the thread is
dropped and the turn is answered fresh, but it costs every conversation its memory.

## Answer decorations

Web answers end with a suggestion element that is not part of the answer:

```
<FollowUp label="…" query="…"/>
```

Strip it — and any `googleusercontent.com` artifact URLs — from the *accumulated*
text, not per delta, because the tag can be split across stream frames.

## Anti-bot constraints

- Use `curl_cffi` with browser impersonation. Plain `httpx`/`requests` expose no TLS
  fingerprint and are not viable against this endpoint.
- Impersonate **`chrome145`**, not `chrome146+`: Chrome 146 on Windows enables Device
  Bound Session Credentials, which binds a session to a device key that a replaying
  client cannot produce.
- Replay **only** `__Secure-1PSID` and `__Secure-1PSIDTS`. Sending the other Google
  cookies is reported to cause HTTP 401 while Google rotates the session.
- Do not hand-write `sec-ch-ua*`/`User-Agent` on top of the impersonation profile.

## Product and operational risk

This design makes every bot user share **one personal Google account**, and that
has consequences worth stating before deployment rather than discovering later:

- **Privacy.** Every user's conversations are written to that account's Gemini
  history. Whoever holds the cookies can read them, and users are not told their
  prompts land in someone else's account. For a multi-user bot this is a real
  disclosure, not a technicality.
- **Shared allowance.** The five-hour compute allowance is per account, not per
  user, so one heavy user exhausts it for everyone until the window resets.
- **Terms of service.** Automated cookie replay is not a supported use of the web
  app and can get the account flagged or locked. The in-stream 1097 rejections are
  a per-account throttle of exactly this kind, and they hit **multi-turn chats
  first**: first turns kept succeeding on the throttled account while every
  follow-up was refused (confirmed 2026-09-22; see above).
- **Recommendation:** use a **dedicated throwaway account**, not the operator's
  main Google account, and treat its cookies as a shared secret. Note the account
  on the deploy ticket so the blast radius is known. Because the throttle is per
  account and multi-turn is the first thing it removes, plan on rotating the
  account rather than treating it as permanent infrastructure.

## Known limitations

- **Lost update on the context row.** The row is read at the start of an
  invocation and written with `put_item` after the answer, so two messages from
  the same user in quick succession — SNS can run them in concurrent Lambdas —
  both start from the same thread ids and the last writer wins. The losing turn's
  `rid`/`context` are dropped, leaving the next follow-up pointing at a stale
  answer, which is itself a plausible way to provoke the rejected follow-ups
  described above. The engine recovers (a failed follow-up is retried as a new
  conversation and the dead thread is dropped), so the blast radius is one
  context-less turn rather than a permanently dead thread, but the row is not
  correctly serialized. The proper fix is a conditional write against a revision
  attribute, or serializing per user.

- **The HTTP timeout must stay below the worker timeout.** `_REQUEST_TIMEOUT` and
  the Lambda's `WORKER_TIMEOUT` (`Duration.minutes(5)`) were both 300 s, so a
  stream that stalls consumes the whole invocation: the Lambda is killed before
  the request can time out, fail over and reply, and because the SQS message was
  never deleted it is redelivered later as a duplicate. The stalled request
  observed live — 3586 bytes, then silence for 180 s — is exactly that shape, so
  `_REQUEST_TIMEOUT` is now set below the worker budget to make the timeout fire
  first. The trade is that an answer needing longer to stream than
  `_REQUEST_TIMEOUT` now fails over instead of arriving.

- **Failures are invisible to the operator.** A dead `__Secure-1PSID` does not
  announce itself: the request fails over to the next chat provider and the user
  still gets an answer, so the bot merely looks slightly less capable. One log
  line and one message already exist, so a CloudWatch metric filter needs no code
  change:
  - `Provider 'gemini' failed` — emitted by the shared runtime on any failover,
    the general "Gemini is down" signal;
  - `Gemini StreamGenerate returned HTTP 400` (or `401`/`403`), and
    `Gemini refused the request` — the expired-credentials signal. These are
    exception messages, so they reach CloudWatch through the traceback the
    failover log line prints rather than as a line of their own.

## How to recapture when Google changes this

1. In a logged-in browser, open DevTools → Network, filter to `StreamGenerate`.
2. Ask one question, then right-click the request → **Copy → Copy as cURL** (or save
   a HAR **with content**, since a plain Chrome HAR omits response bodies).
3. Check the four things that break first: the URL path and query keys, the payload
   slot layout, the answer path (`inner[4][0][1][0]`), and the error wrapper shape.
4. Export the cookies (a DevTools/browser cookie export) and store them as
   `gemini-cookies.json` in the bot bucket. Only `__Secure-1PSID` and
   `__Secure-1PSIDTS` are read.
5. Re-run `tests/test_gemini_web.py` with the captured frames as fixtures — the
   parser is tested against the real framing, so a format change shows up there.

## Seeding credentials

`gemini-cookies.json` in the bot bucket accepts either a browser cookie export (a
list of cookie objects) or a plain `{name: value}` mapping. `gemini-token.json` is
written by the engine and holds `{"access_token": …}`; deleting it just forces a
fresh scrape. Both are read through `BOT_S3_BUCKET` from SSM Parameter Store.

Replacing the account is just replacing this one object: export the cookies while
logged in to the new account and upload them over the same key. Nothing else
identifies the account, so no other configuration changes — and because `at` is
session-scoped rather than account-scoped, a cached `gemini-token.json` from the
old account is worth deleting at the same time to force a fresh scrape.
