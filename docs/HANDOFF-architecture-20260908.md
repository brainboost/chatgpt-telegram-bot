# Handoff — architecture deepening work on chatgpt-telegram-bot

Date: 2026-09-08 · Branch: `architecture` · Audience: the next agent (or the
maintainer) picking this branch up.

This is the session handoff, committed so it survives Windows temp cleanup
(the first copy did not). It is a working note, not a permanent document —
delete it when the branch is merged or parked if it has served its purpose.

## Where the work lives

- Repo: `F:\Projects\Python\chatgpt-telegram-bot`
- Worktree: `.worktrees/architecture` — branch `architecture`; the last code
  commit is `8ad5411` and this note is the commit after it.
- Main worktree on `main` is **untouched**; all work is on this branch.
- Commit series (in order):

| Commit | What |
|---|---|
| `296cfcd` | candidate 1 — typed request message module |
| `0137eac` | candidate 2 — engine session runtime |
| `319067b` | candidate 3 — conversation state made real |
| `a0edf71` | skip the live Ideogram test by default |
| `99e103f` | candidate 4 — one reply-rendering module (engines emit raw content) |
| `2194154` | docs — `CONTEXT.md` + `docs/adr/0001-provider-vocabulary-no-blanket-rename.md` |
| `48d502b` | candidate 5 — provider catalog (`providers.py`) + chat failover chain |
| `d580b8e` | candidate 6 — PTB handler registration once, off the request path |
| `8ad5411` | **prod bugfix** — bind the PTB runtime to each invocation |
| `319f824` | **prod bugfix** — accept both `ig-cookies.json` shapes (`/imagine`) |
| `e9838ee` | **prod bugfix (infra)** — Ideogram result handler timeout + queue redrive |

## Artifacts to read instead of re-deriving

- `CONTEXT.md` (repo root) — domain glossary: provider, request, engine result,
  reply parts, user configuration, conversation context, provider worker.
- `docs/adr/0001-provider-vocabulary-no-blanket-rename.md` — why "provider" is
  the new term while wire/storage keep the word "engine".
- `docs/MARKDOWN_UPGRADE_PLAN.md` — formatting migration plan; its top
  "Adoption note" records which parts candidates 4–5 implemented.
- `lambda/runtime.py` docstring — the loop-lifetime invariant and why it exists.
- `tests/test_runtime.py` — executable documentation of the prod bug.
- Commit messages: detailed and authoritative for every candidate.

## State of the branch

- `uv sync --all-groups` then `uv run pytest tests/` → **82 passed, 5 skipped**
  (skips are live tests needing AWS credentials + a seeded `google_auth.json`
  in `BOT_S3_BUCKET`).
- `uvx ruff check` is clean on every file authored/rewritten plus all engine
  files touched. Remaining findings are **pre-existing legacy noise** in
  `lambda/chatbot.py`, `lambda/utils.py`, `lambda/help_command.py`,
  `lambda/user_config.py` (root-logger `LOG015`, bare `except Exception`
  `BLE001`, async-blocking `ASYNC210/230`).
- Nothing has been deployed from this branch; the prod bug below was fixed
  *after* observing it in the deployed (main) code.

## Prod bug fixed here (read this first)

Commit `8ad5411`.

- **Symptom:** commands intermittently fail with
  `telegram.error.NetworkError: Unknown error in HTTP implementation:
  RuntimeError('Event loop is closed')`; the user gets no reply and the Lambda
  still returns 200.
- **Cause:** one PTB `Application` per container, one `asyncio.run` per
  invocation. The runtime stayed initialized across invocations, so the next
  invocation reused pooled keep-alive connections created on the previous (now
  closed) loop; closing such a connection calls `loop.call_soon` on a dead
  loop. It looked intermittent because the broken connection is discarded after
  the failure, so the following invocation succeeds.
- **Fix:** `lambda/runtime.py` owns the Lambda's PTB hosting —
  `create_application(token)` and `process_update_event(event, app)` bracket
  every invocation with `initialize()` / `shutdown()`, so nothing loop-bound
  outlives its loop. `chatbot.py` delegates to it. Cost: one extra `getMe` per
  invocation (accepted).
- **Also:** `error_handle` existed but was never registered as the Application
  error handler; PTB therefore logged the traceback itself and swallowed the
  exception. It is registered now.
- **Verify after redeploy:** send two messages in quick succession (same warm
  container — the second used to fail silently) on each command surface, then
  confirm "Event loop is closed" stops appearing in CloudWatch.
- **Alternative considered:** one long-lived event loop per container
  (`loop.run_until_complete`) avoids the per-invocation `getMe`, but keeps
  runtime state tied to a frozen loop; revisit only if latency shows up.

### Second prod bug: `/imagine` (cookie shape)

Commit `319f824`.

- **Symptom:** `TypeError: list indices must be integers or slices, not str` in
  `engines/ideogram_img.py` `request_images`; every `/imagine` failed.
- **Cause:** `ig-cookies.json` has two legitimate shapes — the mapping the
  engine writes (`{name: value}` from `dict(response.cookies)`) and the browser
  export a human seeds (a list of cookie objects with `name`/`value`). The
  seeded file is the export, so `cookies["session_cookie"]` indexed a list.
- **Fix:** `engines/ideogram_cookies.py` normalizes either shape (junk degrades
  to "no cookie", which triggers the login refresh); `request_images` reads the
  session cookie through it. The dead `json_cookies_to_header_string` helper is
  gone.
- **Testability:** `ideogram_img` resolved SSM/SQS at import, which is why this
  never had offline coverage — it now resolves them lazily, so
  `tests/test_ideogram_img.py` can drive `request_images` through both shapes,
  the refresh path and the API-error path with AWS/network stubbed. That test
  fails with the exact prod `TypeError` before the fix.
- **Also fixed while in there:** the session cookie was being dumped into
  CloudWatch — `ideogram_img` and `ideogram_result` logged whole payloads that
  carry the `Cookie` header.
- **Verified against the real bucket:** with only the Ideogram POST stubbed, the
  1079-char session cookie reaches the `Cookie` header. The seeded list-shaped
  file does **not** need to be replaced; both shapes now work.

### Third prod bug: `/imagine` result handler timeout (infrastructure)

Commit `e9838ee`.

- **Symptom:** images were generated (visible in the Ideogram dashboard) but
  nothing reached Telegram; CloudWatch showed `IdeogramResultHandler` ending in
  `Status: timeout` at 3000 ms, repeatedly, and one message stuck in flight on
  `Ideogram-Result-Queue`.
- **Cause:** the result handler is created inline in `stacks/engines_stack.py`
  rather than through `__create_engine`, so it inherited Lambda's **3-second /
  128 MB** defaults while every other worker runs 300 s / 256 MB. A
  "not ready yet" poll fits in 3 s, but the invocation that finally has URLs does
  not (Ideogram call ~0.8 s, `RESULT_SNS_TOPIC_ARN` SSM read ~1.6 s, SNS client,
  publish), so it was killed mid-publish: `ResultProcessingHandler` (the Telegram
  sender) had not run since 17:16 UTC, and the message was redelivered every 5 s.
- **Fix:** `__worker_defaults()` extracted so no worker can silently miss a
  timeout; the result handler runs 60 s / 256 MB; queue visibility raised 5 s →
  360 s (6× the timeout); redrive policy added (maxReceiveCount 5 →
  `Request-Queues-DLQ`) so a poison message stops looping and trips the existing
  alarm.
- **Validated** by synthesizing `EnginesStack` with `aws-cdk-lib` (the `cdk` CLI
  is not installed here): the template carries Timeout 60 / MemorySize 256,
  VisibilityTimeout 360 and the RedrivePolicy, and other workers are unchanged.
- **Deploy:** needs `cdk deploy EnginesStack`. A message already over
  maxReceiveCount will be moved to the DLQ instead of processed, so re-run
  `/imagine` or redrive `Request-Queues-DLQ` manually (the admin `/redrive`
  command only understands SNS-wrapped bodies).

## Deliberate behavior changes (verify on a canary)

Approved in-session; not regressions:

1. `/reset` now actually reaches the providers and clears conversation memory.
2. `/ping` answers pong without writing a conversation turn.
3. Chat engines send up to 8 prior turns (multi-turn memory is on).
4. `user-conversations` / `request-jobs` are no longer written; image and
   translation flows are stateless.
5. Engines publish **raw** content plus a `format` flavor (`markdown` default,
   `plain` for DeepL, legacy `markdownv2` passthrough); the sender renders once
   (`lambda/formatting.py`). Gemini's bespoke `__as_markdown` is gone, so
   Gemini/DeepL rendering can differ slightly from before.
6. `/engines` (parallel multi-provider) is **removed**; `/gemini`, `/qwen`,
   `/llama` set the conversation-start provider (persisted in the user-config
   `engines` field, legacy name).
7. Chat failover chain `gemini → qwen → llama`: a failing provider re-publishes
   the request to the next one; the tail replies a plain-text error instead of
   DLQ-ing. Outage turns are not stored in history; context stays per-provider
   (a qwen/llama outage answer is not visible to the next gemini turn).
8. Unknown `/commands` now get a plain "Unknown command" reply instead of
   falling through to an engine request.
9. `/engines`-era stored configs holding several ids keep working: only the
   first id is used as the chain start.
10. `/imagine` now sends the payload shape Ideogram's own web client uses:
    `model_version: "AUTO"` with a derived `model_uri`,
    `use_autoprompt_option: "AUTO"`, `sampling_speed: 2`, `style_type` (renamed
    from `style_expert`), and `num_images: 4` — one prompt therefore returns
    **four images**. Resolution deliberately stays square 1024×1024 (the
    captured request used 1280×800 landscape). The payload lives in
    `engines/ideogram_request.py` (plain dataclass; the engines bundle carries no
    pydantic) and the wire contract is pinned by a test built from a real
    captured request.

## Suggested next session focus — E2E/canary verification

One surface at a time against the `dev` stage: chat text → **two messages in a
row (warm container, the loop bug)** → `/tr` (with and without arguments) →
`/imagine` and `/ideogram` → voice note → photo/attachment with caption →
`/gemini` `/qwen` `/llama` switch and persistence across a new session →
`/reset` clearing memory on every chat provider → unknown command reply →
**failover drill** (force a Gemini failure and confirm qwen, then llama, then
the tail error text; confirm nothing lands in the request DLQ) → multi-part
long answer (>4060 chars) → emoji-heavy answer (UTF-16 splitter).

## Open follow-ups (deliberately not done)

- **CDK data-driven workers**: `stacks/engines_stack.py` still hand-writes one
  block per provider; iterate `providers.py` instead. No CDK tests exist —
  validate with `cdk synth` + a dev deploy.
- **Shared-package question**, partially answered: `providers.py` is a
  top-level pure-stdlib module copied into both bundles by
  `scripts/build_bundles.py`. Other cross-bundle duplication (typed engine-side
  models, the SNS attribute shape) is still unresolved.
- **Repo-wide ruff hygiene**: fix or codify ignores for legacy
  `LOG015`/`BLE001`/`ASYNC*`.
- **Orphan S3 uploads** in `chatbot.py:process_upload` (uploaded attachment
  path is never consumed by an engine).
- **Unused infra**: `user-conversations` and `request-jobs` tables are still
  defined with `RemovalPolicy.RETAIN`; left alone to avoid a CFN change on
  retained resources.
- **OpenRouter-style provider**: maintainer wants one provider id fronting many
  models; `providers.py` + the failover chain are the intended seam.
- **Rename staging**: a full `engines` → `providers` rename of the wire
  attribute and stored keys is *not* planned (see ADR-0001).
- **Per-invocation `getMe`**: if it shows up in latency, consider caching the
  bot identity or the long-lived-loop alternative above.

## Working rules for this repo (learned the hard way)

- Two separate uv projects: `lambda/` and `engines/` cannot import each other;
  only `providers.py` is shared, because the bundler copies it.
- Run `uv sync --all-groups` before `uv run pytest`: `uv run` syncs only the
  default groups and will prune `telegram`/`boto3`, breaking imports.
- Run the suite as `uv run pytest tests/` from the repo root; a bare `pytest`
  also collects duplicated `tests/` copies under `cdk.out`.
- `lambda` is a Python keyword: import it with
  `importlib.import_module("lambda.<module>")`.
- `lambda/chatbot.py` cannot be imported in tests (module-level SSM reads).
  Put logic in small pure modules instead — `lambda/runtime.py` shows the
  pattern (runtime seam) and `_chat_provider` shows it for bot logic.
- PTB **swallows handler exceptions** when no error handler is registered; a
  logged traceback does not mean the invocation failed (the Lambda returns
  200). Assert on observable effects (delivery), not on raised exceptions.
- An external formatter may rewrite files between edits; re-read before
  editing and check `git status` for stray changes.
- Approval prompts are disabled: sandbox escalations are rejected — restructure
  the command instead.
- Secrets live in SSM (parameter names only in code); never print values.

## Suggested skills

- `diagnose` — the prod loop bug was found this way; reuse the pattern (build a
  loop that asserts the *observable* symptom before hypothesising).
- `tdd` — for the OpenRouter-style provider or the CDK catalog loop.
- `improve-codebase-architecture` — re-run once merged; `CONTEXT.md` and
  `docs/adr/` now exist and should inform it.
- `prototype` — if the OpenRouter strategy needs design exploration first.
- `handoff` — for the next handoff when this branch is merged or parked.
