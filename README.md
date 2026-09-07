# ChatGPT Telegram Bot

A serverless Telegram bot that answers with multiple AI engines, generates images and
translates text. Built on AWS with the AWS CDK: Lambda functions running container images,
SNS/SQS for asynchronous messaging, DynamoDB for state and S3 for storage.

- **AI chat**: Gemini (Google), plus **Qwen 3.5** and **Llama 4** via Ollama Cloud — enable
  several engines and they answer your message **in parallel**
- **Images**: Ideogram (native text/typography rendering)
- **Translation**: DeepL (interactive multi-language flow)
- **Voice**: voice messages are transcribed with Amazon Transcribe and sent to your engines
- **Deployment**: two-stage (dev → prod) via CDK CLI or GitHub Actions (OIDC)

---

## Table of contents

1. [Bot commands](#bot-commands)
2. [Engines](#engines)
3. [Architecture](#architecture)
4. [Repository layout](#repository-layout)
5. [Local development](#local-development)
6. [Deployment (dev / prod)](#deployment-dev--prod)
   - [Staging model](#staging-model)
   - [Prerequisites per stage](#prerequisites-per-stage)
   - [One-time bootstrap](#one-time-bootstrap)
   - [Manual deployment (CLI)](#manual-deployment-cli)
   - [CI/CD deployment (GitHub Actions)](#cicd-deployment-github-actions)
   - [Undeploying](#undeploying)
7. [Post-deploy checks & troubleshooting](#post-deploy-checks--troubleshooting)
8. [Configuration reference](#configuration-reference)
9. [Tests](#tests)

---

## Bot commands

Reply formatting uses Telegram MarkdownV2. Replies longer than one message are split into
parts labelled `i of n`. In **groups**, the bot only reacts when it is addressed
(`@<bot_username>` in the message/caption).

### User commands

| Command | Description |
|---|---|
| `/start` | Welcome message and list of supported commands |
| `/help <command>` | Help for one command, e.g. `/help tr`, `/help engines`, `/help imagine` |
| `/engines` | Show the currently active engines |
| `/engines <list>` | Activate engines for parallel answering, comma separated — `/engines gemini,llama,qwen`. Persists in your user configuration |
| `/gemini` · `/llama` · `/qwen` | Shortcuts to switch to a **single** engine |
| `/reset` | Reset the conversation/context of the currently active engines |
| `/creative` · `/balanced` · `/precise` | Set the tone of responses (stored in your user configuration) |
| `/imagine <prompt>` · `/ideogram <prompt>` | Generate an image with Ideogram — e.g. `/imagine cute kitty plays with a yarn ball` |
| `/tr` | Interactive translation: pick target language(s), then send the text |
| `/tr <langs>` | One-shot translation — `/tr pl,ru` then send the text (DeepL codes, comma separated) |
| `/cancel` | Cancel an active `/tr` conversation |

**Non-command input**

- Plain text → sent to your active engines.
- Voice messages → transcribed, the transcript is shown to you *and* sent to your engines.
- Photos / documents / attachments → uploaded to S3 and fanned out to your active engines
  (engine-level file handling varies by provider).

### Admin commands (restricted to `TELEGRAM_BOT_ADMINS`)

| Command | Description |
|---|---|
| `/ping` | Health check — replies `pong` from each active engine |
| `/errors` | Tail CloudWatch Logs errors (last 3h) from the Lambda log groups |
| `/redrive` | Move messages stuck in the SQS DLQs back to their SNS topics |

> ⚠️ Admin limitation: `TELEGRAM_BOT_ADMINS` currently supports **one** user id — see
> [Configuration reference](#configuration-reference).

---

## Engines

| Engine | Provider | Purpose | Handler |
|---|---|---|---|
| `gemini` | Google Gemini 3 (`gemini-3.8-flash`, GA) | Text chat | `engines/gemini.py` |
| `qwen` | Alibaba Qwen 3.5 (Ollama Cloud, `qwen3.5:cloud`) | Text chat (sync) | `engines/ollama.py` |
| `llama` | Meta Llama 4 (Ollama Cloud, `llama4:maverick`) | Text chat (sync) | `engines/ollama.py` |
| Ideogram | ideogram.ai | Image generation (`/imagine`, `/ideogram`), async via polled queue | `engines/ideogram_img.py`, `engines/ideogram_result.py` |
| DeepL | DeepL API | Translation (`/tr`) | `engines/deepl_tr.py` |

The `qwen` and `llama` engines share one OpenAI-compatible Ollama Cloud handler; CDK sets the
model tag per Lambda via the `OLLAMA_MODEL`/`OLLAMA_ENGINE` environment variables
(override the tags to anything your Ollama account exposes under
https://ollama.com/search?c=cloud).

Engine handlers expose an SNS `sns_handler` (or `sqs_handler`/`callback_handler`) and are
deployed as separate Lambda functions; see `stacks/engines_stack.py`.

---

## Architecture

Three CDK stacks (`app.py`):

1. **EnginesStack** — SNS request topic, per-engine Docker Lambda functions (DLQ + alarms)
   and the Ideogram polling queue/result handler.
2. **DatabaseStack** — DynamoDB tables: `user-configurations`, `user-conversations`,
   `user-context` (TTL 60 d), `request-jobs` (TTL 10 d).
3. **ChatBotStack** — Telegram bot Lambda exposed via a Function URL (webhook target),
   result-processing Lambda, webhook trigger, S3 bucket, alarms. Depends on the two stacks
   above.

Request flow:

```
Telegram webhook ──► BotHandler (Function URL)
        │  publish (SNS request-ai-topic, filter attributes: type / engines)
        ▼
   ┌─────────────── engine Lambdas (Gemini, LLama, Qwen, Ideogram, DeepL) ─────────┐
   │ Ideogram: result polled via SQS delayed queue; LLama: result via webhook      │
   └───────────────► publish (SNS result-ai-topic) ◄───────────────────────────────┘
                        ▼
              ResultProcessingHandler ──► Telegram replies
```

Configuration is read from **SSM Parameter Store at Lambda cold start** and state lives in
DynamoDB. All Lambda functions run the same container image built from the repo-root
`Dockerfile`; the entry point is chosen per function via the CDK `cmd`
(e.g. `lambda.chatbot.telegram_api_handler`).

---

## Repository layout

```
app.py                     CDK entry point (defines the 3 stacks)
stacks/                    CDK stack definitions (chatbot, engines, database)
lambda/                    Telegram bot Lambdas: chatbot, results, webhook, utils, help
engines/                   AI engine handlers: gemini, ollama (llama + qwen), ideogram, deepl + shared utils
tests/                     pytest tests (live tests skipped without AWS)
Dockerfile                 Multi-stage Lambda image (Python 3.14, uv + lockfiles)
pyproject.toml             Root project: dev/CDK deps + lambda/engines dependency groups
UPDATE_VARS.md             AWS configuration checklist (SSM params, S3 auth files, secrets)
docs/MARKDOWN_UPGRADE_PLAN.md   Plan for Telegram Rich Messages / formatting migration
```

Each of `lambda/` and `engines/` is an independent uv project with its own `pyproject.toml`
and `uv.lock`; the root project aggregates them as dependency groups for local dev.

---

## Local development

Requirements: Python 3.14+ and [uv](https://docs.astral.sh/uv/), Node.js 20+ / `aws-cdk`
CLI, Docker (BuildKit), AWS credentials.

```bash
uv sync --all-groups            # install dev/CDK + lambda + engines deps into .venv

uv run pytest                   # unit tests (AWS/live tests are @pytest.mark.skip)
```

Keep lockfiles in sync after editing `pyproject.toml`:

```bash
uv lock                                # root project (dev/CDK tooling)
(cd lambda  && uv lock)                # bot Lambda deps
(cd engines && uv lock)                # engine deps
```

---

## Deployment (dev / prod)

The project supports two deployment stages, `dev` and `prod`. Both are deployed with the
same CDK code; the `STAGE` value is only used in a few resource names (e.g. the S3 bucket
`chatbotstack-s3-bucket-<stage>-tmp`).

### Staging model

| | **dev** | **prod** |
|---|---|---|
| Purpose | Sandbox / continuous integration | Stable, user-facing |
| Git branch (CI trigger) | `development` (push) | `main` (push) or manual |
| AWS account | Separate dev account **(recommended)** | Production account |
| Region | Any (e.g. `eu-central-1`) | Any |
| `STAGE` | `dev` | `prod` |
| GitHub environment | `dev` | `prod` |

> ⚠️ **Accounts and regions — read this before deploying**
> Most resource names are **not** stage-scoped: DynamoDB tables, SNS topics, SQS queues,
> Lambda function names/log groups and the SSM parameters are fixed. Therefore **dev and prod
> cannot run in the same account+region at the same time** — name collisions and clobbered
> SSM values. Either give each stage its own AWS account (recommended), or run them in
> different regions of one account.

### Prerequisites per stage

Before the first deployment to an account/region, complete the checklist in
[`UPDATE_VARS.md`](UPDATE_VARS.md). In short:

1. Create the **manual SSM parameters** (`TELEGRAM_TOKEN`, `TELEGRAM_BOT_ADMINS`,
   `SECRET_TOKEN`, `ALARM_EMAIL`, `GEMINI_API_KEY`, `DEEPL_AUTHKEY`, `OLLAMA_API_KEY`,
   `IDEOGRAM_USER`) — all as `Type=String` (see the caveat in `UPDATE_VARS.md`).
2. Seed the S3 auth file **`google_auth.json`** (Ideogram refresh token) — the bucket is
   created by ChatBotStack, so upload the file right after the first deploy and before the
   first `/imagine`.
3. Confirm the SNS **alarm e-mail** subscription (one click in the inbox).
4. Have Docker running (image assets are built during synth/deploy).

### One-time bootstrap

Each (account, region) pair must be bootstrapped once before the first CDK deploy:

```bash
cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
```

> ⚠️ **The bootstrap ("CDKToolkit") stack version is tied to the CDK CLI it was created
> with, not to the code.** After upgrading `aws-cdk-lib` — this repo now pins 2.268.x, which
> requires bootstrap **v30+** — re-run the same `cdk bootstrap` command **before** the next
> deploy. Otherwise `cdk deploy` aborts with *"Bootstrap toolkit stack version 30 or later is
> needed; current version: 27"* and, because the deploy role's permissions come from the
> toolkit stack, it may also lack `cloudformation:DescribeEvents` (only granted by the newer
> bootstrap template) → `AccessDenied` while reporting change-set validation failures.
> Bootstrapping is idempotent: re-running it with a current CLI updates the toolkit stack and
> its IAM roles in place. It needs CloudFormation + IAM rights on the `CDKToolkit` stack, so
> run it with the account owner/administrator.

### Manual deployment (CLI)

```bash
# 1. Configure the target stage
export CDK_ACCOUNT=<ACCOUNT_ID>
export CDK_REGION=<REGION>
export STAGE=dev                 # or prod

# 2. (If not yet installed) the CDK CLI
npm install -g aws-cdk

# 3. Install Python deps and sanity-check
uv sync --all-groups
cdk ls                           # EnginesStack  DatabaseStack  ChatBotStack

# 4. Review what will change
cdk diff

# 5. Deploy everything (order/dependencies are resolved automatically)
cdk deploy --all --require-approval never

# 6. Deploy a single stack if needed (respects dependencies)
cdk deploy ChatBotStack

# 7. Verify the stage (see "Post-deploy checks" below)
```

Notes:

- `cdk deploy --all` runs EnginesStack, DatabaseStack, ChatBotStack in dependency order;
  ChatBotStack's webhook trigger registers the Telegram webhook at the end of the deploy.
- Deploying requires Docker (container image assets are built during synthesis/deploy).
- If you change only Lambda code, redeploy ChatBotStack and/or EnginesStack (the affected
  image layer is rebuilt).

### CI/CD deployment (GitHub Actions)

Two workflows are provided (`.github/workflows/deploy-dev.yml`, `deploy-prod.yml`). They use
OpenID Connect and never store long-lived AWS keys:

| Workflow | Trigger | `STAGE` | GitHub environment |
|---|---|---|---|
| `deploy-dev.yml` | push to `development`, or `workflow_dispatch` | `dev` | `dev` |
| `deploy-prod.yml` | push to `main`, or `workflow_dispatch` | `prod` | `prod` |

Pipeline steps (both files): checkout → assume AWS role (OIDC) → setup Node 22 + Python 3.14 →
`uv sync --all-groups` → `cdk bootstrap` (idempotent — upgrades a missing **or stale**
CDKToolkit stack) → `cdk deploy --all --require-approval never`.

Required repository/environment secrets:

| Secret | Meaning |
|---|---|
| `AWS_ROLE` | ARN of the IAM role the workflow assumes (trusted by the GitHub OIDC provider) |
| `AWS_REGION` | Region the workflow deploys to |

Per environment: create a `dev` and a `prod` environment in **Settings → Environments**, each
with its own `AWS_ROLE`/`AWS_REGION`, pointing at its dedicated AWS account. Create the SSM
parameters listed in [`UPDATE_VARS.md`](UPDATE_VARS.md) in each account **before** the first
workflow run (the workflows do not provision secrets).

> The workflows bootstrap before deploying, so a missing **or outdated** toolkit stack is
> upgraded automatically on the next run — **provided the `AWS_ROLE` may update the
> `CDKToolkit` stack** (bootstrap needs CloudFormation + IAM rights). If your role is
> deploy-only, run `cdk bootstrap aws://<ACCOUNT>/<REGION>` once manually with an
> administrator, and re-run it after every `aws-cdk-lib` upgrade that raises the required
> bootstrap version (the deploy fails with a *"Bootstrap toolkit stack version … is needed"*
> error until you do).

### Undeploying

```bash
cdk diff                 # inspect
cdk destroy --all        # tears down stacks (see note below)
```

`RemovalPolicy` note: the S3 bucket, queues and log groups are destroyed with the stack,
but the **DynamoDB tables are `RETAIN`ed** and remain after `cdk destroy` (delete them
manually if you really want them gone).

---

## Post-deploy checks & troubleshooting

1. **Webhook registered**: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo` should
   show the `BOT_LAMBDA_URL` and your `SECRET_TOKEN`.
2. **Alarm e-mail**: confirm the SNS subscription sent to `ALARM_EMAIL`.
3. **Bot answers**: send `/start`, then `/ping` (admin) — each active engine should reply
   `pong`.
4. **Images**: send `/imagine <prompt>` — if it fails, check `google_auth.json` in the bot
   bucket (`BOT_S3_BUCKET`) and `IDEOGRAM_USER` consistency.
5. **Secrets rotation**: SSM values are read at Lambda cold start; after rotating a value,
   update SSM **and** redeploy so warm instances pick it up.
6. **Lambda crashes at cold start with SSM errors**: a parameter from `UPDATE_VARS.md` is
   missing, or was created as `SecureString` (must be `String`).

---

## Configuration reference

Every runtime parameter, S3 auth/cookie file, deploy variable and CI secret — with exact
names, where each is read, example values and `aws` CLI snippets — lives in
[`UPDATE_VARS.md`](UPDATE_VARS.md). Use it as the checklist for every new stage/account.

---

## Tests

```bash
uv run pytest
```

Unit tests live in `tests/`. Tests that require live AWS resources or external APIs are
marked `@pytest.mark.skip` and only run against a fully configured account.
