# ChatGPT Telegram Bot

A serverless Telegram bot that answers with several AI engines, generates images and
translates text. Built on AWS with the AWS CDK: Lambda functions running container
images, SNS/SQS for async messaging, DynamoDB for state and S3 for storage.

## AI engines

| Engine | Purpose | Handler |
| ------ | ------- | ------- |
| Gemini (Google) | Text chat | `engines/gemini.py` |
| LLama 2 (MonsterAPI) | Text chat (async, webhook callback) | `engines/monsterapi.py` |
| Ideogram | Image generation (async, polled) | `engines/ideogram_img.py` |
| DeepL | Translation | `engines/deepl_tr.py` |

Voice notes are transcribed with Amazon Transcribe and then sent to the configured engines.

## Architecture

Three CDK stacks (defined in `app.py`):

1. **EnginesStack** – SNS request topic, per-engine Docker Lambda functions (with DLQ
   and alarms), plus the Ideogram polling queue and the MonsterAPI webhook callback.
2. **DatabaseStack** – DynamoDB tables: `user-configurations`, `user-conversations`,
   `user-context` (TTL), `request-jobs` (TTL).
3. **ChatBotStack** – Telegram bot Lambda (Function URL as webhook target), result
   processing Lambda, webhook trigger, S3 bucket, alarms. Depends on the other two.

Request flow: Telegram webhook → `BotHandler` → SNS `request-ai-topic` (filtered by
engine) → engine Lambda → SNS `result-ai-topic` → `ResultProcessingHandler` → Telegram.

## Bot commands

`/start`, `/help <command>`, `/engines <list>` (parallel engines), `/llama`, `/gemini`,
`/reset`, `/creative|balanced|precise` (tone), `/imagine|/ideogram <prompt>`,
`/tr <lang(s)>`, and admin commands `/ping`, `/errors`, `/redrive`.

## Requirements

- Python 3.14+ and [uv](https://docs.astral.sh/uv/) (package manager).
- Node.js 20+ and the `aws-cdk` CLI (`npm install -g aws-cdk`) for deployment.
- Docker with BuildKit for building the Lambda container images.
- AWS credentials (`CDK_ACCOUNT`, `CDK_REGION` environment variables).

## Development

```bash
# Install all dependencies (root project: dev tools + lambda + engines groups)
uv sync --all-groups

# Locks are maintained per project:
uv lock                      # root (dev/CDK tooling)
cd lambda && uv lock
cd engines && uv lock
```

Configuration is read at Lambda startup from AWS SSM Parameter Store. Required
parameters: `TELEGRAM_TOKEN`, `TELEGRAM_BOT_ADMINS`, `SECRET_TOKEN`, `ALARM_EMAIL`,
`BOT_LAMBDA_URL`, `BOT_S3_BUCKET`, `REQUESTS_SNS_TOPIC_ARN`, `RESULT_SNS_TOPIC_ARN`,
`MONSTERAPI_CALLBACK_URL`, `MONSTERAPI_TOKEN`, `DEEPL_AUTHKEY`, `GEMINI_API_KEY`,
`IDEOGRAM_USER`. Most are created automatically by the stacks.

## Deploy

```bash
cdk ls                 # list stacks
cdk synth              # synthesize CloudFormation
cdk deploy --all       # deploy (requires Docker for image assets)
cdk diff
cdk destroy --all
```

`STAGE` selects the environment suffix used by resource names (default `prod`).

## Lambda container image

All functions share one image built from the repo-root `Dockerfile` (the function to
run is selected per Lambda via the CDK `cmd`, e.g. `lambda.chatbot.telegram_api_handler`).
The Dockerfile is a multi-stage build on `public.ecr.aws/lambda/python:3.14`: locked
dependencies of both `lambda/` and `engines/` are exported from their committed
`uv.lock` files (`uv export --frozen`) and installed into `LAMBDA_TASK_ROOT`; `uv` and
the build stage stay out of the final image.

## Tests

```bash
uv run pytest
```

Tests requiring live AWS resources or external APIs are `@pytest.mark.skip`ped and only
run against a configured environment.
