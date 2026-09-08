# UPDATE_VARS.md — AWS configuration checklist for the deployed bot

Everything the deployed application reads from AWS at runtime, what must exist **before**
`cdk deploy`, what is created automatically, and what to refresh after a rollout.

All values live in **one AWS account/region** (the one Lambda runs in). Runtime secrets are
stored in **SSM Parameter Store** and read by the Lambdas at cold start (module import time),
not from Lambda environment variables.

> ⚠️ **Read before you deploy — SSM parameter type must be `String`**
> All runtime code reads parameters with `ssm.get_parameter(Name=...)` **without**
> `WithDecryption=True` (`lambda/utils.py`, `lambda/webhook.py`, `engines/common_utils.py`).
> If you create a parameter as `SecureString`, the Lambdas receive the **encrypted blob** and
> the bot breaks. Create every parameter below as **`Type=String`** (plaintext). (If you want
> SecureString, the code must first be updated to pass `WithDecryption=True`.)

---

## 1. Manual SSM parameters — create BEFORE deploying (required)

Create these in Parameter Store first; the CDK stacks and Lambdas expect them to exist at
deploy/cold-start time.

| Parameter name | Read by | Value / example | Notes |
|---|---|---|---|
| `TELEGRAM_TOKEN` | BotHandler, ResultProcessingHandler, WebhookTriggerHandler | `123456789:AAH...` (from @BotFather) | Must be `Type=String`. |
| `TELEGRAM_BOT_ADMINS` | BotHandler (`lambda/chatbot.py`) | one Telegram numeric user id, e.g. `424242424` | ⚠️ **Known limitation:** the code reads it as a *single* value (`admins = [param]`) and does not split commas, so comma-separated ids do **not** all become admins. Either store exactly **one admin id**, or fix `lambda/chatbot.py:51` to `.split(",")`. |
| `SECRET_TOKEN` | WebhookTriggerHandler (`lambda/webhook.py`) | any long random string | Used as the webhook `secret_token`; must exist **before** deploy (the trigger registers the webhook during deploy). Same value is then required by Telegram on every update. |
| `ALARM_EMAIL` | EnginesStack + ChatBotStack (SNS alarm subscriptions) | `ops@example.com` | Resolved at **deploy time**; the SNS email subscription must be **confirmed** from the inbox or alarms stay silent. |
| `GEMINI_API_KEY` | Gemini engine (`engines/gemini.py`) | Google AI Studio API key | Read lazily on first Gemini request. |
| `DEEPL_AUTHKEY` | DeepL engine (`engines/deepl_tr.py`) | DeepL API auth key | — |
| `OLLAMA_API_KEY` | LLama 4 + Qwen engines (`engines/ollama.py`) | Ollama Cloud API key (https://ollama.com/settings/keys) | Free plan: 1 concurrent request, monthly starter credits; both engines share the key |
| `IDEOGRAM_USER` | Ideogram engine (`engines/ideogram_img.py`) | Firebase user id, e.g. `abc123...` | Must match the `user_id` inside `google_auth.json` (see §3). |

Example (AWS CLI, run in the target region/account):

```bash
aws ssm put-parameter --name TELEGRAM_TOKEN     --type String --value "123456789:AAH..." --overwrite
aws ssm put-parameter --name TELEGRAM_BOT_ADMINS --type String --value "424242424"       --overwrite
aws ssm put-parameter --name SECRET_TOKEN        --type String --value "$(openssl rand -hex 32)" --overwrite
aws ssm put-parameter --name ALARM_EMAIL         --type String --value "ops@example.com" --overwrite
aws ssm put-parameter --name GEMINI_API_KEY      --type String --value "AIza..." --overwrite
aws ssm put-parameter --name DEEPL_AUTHKEY       --type String --value "deep://..." --overwrite
aws ssm put-parameter --name OLLAMA_API_KEY      --type String --value "ollama_..." --overwrite
aws ssm put-parameter --name IDEOGRAM_USER       --type String --value "<firebase-uid>" --overwrite
```

**Per-engine model tags** (Lambda env, set by EnginesStack; change + redeploy to override):
`llama` → `llama4:maverick`, `qwen` → `qwen3.5:cloud` (env `OLLAMA_MODEL` / `OLLAMA_ENGINE`).
Cloud model availability varies per Ollama account — verify the exact tag your account offers
under https://ollama.com/search?c=cloud before relying on an engine.

---

## 2. Parameters auto-created by CDK — do NOT create manually

| Parameter name | Created by | Value |
|---|---|---|
| `REQUESTS_SNS_TOPIC_ARN` | EnginesStack | ARN of the `request-ai-topic` SNS topic |
| `RESULT_SNS_TOPIC_ARN` | ChatBotStack | ARN of the `result-ai-topic` SNS topic |
| `BOT_LAMBDA_URL` | ChatBotStack | Function URL of BotHandler (webhook target) |
| `BOT_S3_BUCKET` | ChatBotStack | Name of the bot's S3 bucket (see §3) |

---

## 3. Configuration files / cookies in S3 (bucket = value of `BOT_S3_BUCKET`)

The Ideogram engine reads two JSON "auth files" from the **root of the bot bucket** (bucket
name is the `BOT_S3_BUCKET` parameter, e.g. `chatbotstack-s3-bucket-prod-tmp`):

### `google_auth.json` — REQUIRED seed (engine can auto-refresh it afterwards)
Schema read by `engines/ideogram_img.py` (`check_and_refresh_auth_tokens`):

```json
{
  "refresh_token": "1//0g....",
  "access_token": "ya29....",
  "user_id": "abc123..."
}
```

- **`refresh_token` is mandatory** — without it the engine errors (`No 'refresh_token' found`).
- `access_token` and `user_id` are refreshed/saved automatically by the engine when the
  access token is missing/expired.
- How to obtain it initially: log in to ideogram.ai in a browser, open DevTools → Network,
  capture the Firebase/Google auth exchange (requests to `securetoken.googleapis.com` /
  `identitytoolkit.googleapis.com` or the initial page session), and save `refresh_token` +
  `user_id` into the file. It is then stored at the bucket root.
- `user_id` here must equal the `IDEOGRAM_USER` SSM parameter (§1).

```bash
BUCKET=$(aws ssm get-parameter --name BOT_S3_BUCKET --query Parameter.Value --output text)
aws s3 cp google_auth.json s3://$BUCKET/google_auth.json
```

### `ig-cookies.json` — OPTIONAL seed (auto-created)
Ideogram session cookies. The engine creates and refreshes this file itself
(`get_session_cookies` → `save_to_s3`), so it may start **absent**; only add it manually to
pre-seed a session. Key used by the engine: `session_cookie`.

### Removed with the Claude engine and the MonsterAPI migration (no longer needed — safe to delete)
- SSM `CLAUDE_API_KEY` + S3 `claude-cookies.json` (Claude engine removed).
- SSM `MONSTERAPI_TOKEN` / `MONSTERAPI_CALLBACK_URL` + SQS queue `MonsterApi-Callback-DLQ` +
  Lambda `MonsterApiCallbackHandler` (MonsterAPI is offline since ~Sept 2025; the LLama
  engine now uses Ollama Cloud via `OLLAMA_API_KEY`).

---

## 4. Deploy-time variables (local/CDK CLI)

| Variable | Used for | Example |
|---|---|---|
| `CDK_ACCOUNT` | CDK environment | `123456789012` |
| `CDK_REGION` | CDK environment | `eu-central-1` |
| `STAGE` | Resource name suffix (`ChatBotStack` S3 bucket, etc.) | `dev` / `prod` (default `prod`) |

```bash
export CDK_ACCOUNT=123456789012 CDK_REGION=eu-central-1 STAGE=prod
npx aws-cdk deploy --all
```

GitHub Actions already pins `PYTHON_VERSION: 3.14` and `NODE_VERSION: 22` in
`.github/workflows/deploy-*.yml` — no action needed unless you bump the runtimes.

## 5. GitHub Actions / CI secrets (repo-level)

| Secret | Used by | Value |
|---|---|---|
| `AWS_ROLE` | `aws-actions/configure-aws-credentials@v4` (`role-to-assume`) | IAM role ARN the workflow assumes (OIDC) |
| `AWS_REGION` | same action | Target region |

Environment-scoped: the `dev`/`prod` GitHub environments carry their own `AWS_ROLE`/
`AWS_REGION` and set `STAGE`.

---

## 6. Post-deploy verification & periodic refreshes

1. **Alarms e-mail** — confirm the SNS subscription sent to `ALARM_EMAIL` after first deploy.
2. **Webhook** — WebhookTriggerHandler runs during deploy; verify with
   `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo` → URL = `BOT_LAMBDA_URL`.
3. **Ideogram** — send `/imagine <prompt>`; if it fails, re-check `google_auth.json`
   (`refresh_token`) and `IDEOGRAM_USER` consistency. Access tokens and session cookies are
   auto-refreshed; only a dead `refresh_token` (or Ideogram changing auth) needs a manual
   `google_auth.json` update.
4. **Secret rotation** — parameters are read at Lambda cold start (module import). After
   rotating `TELEGRAM_TOKEN` / `OLLAMA_API_KEY` / `DEEPL_AUTHKEY` / `IDEOGRAM_USER`, update
   SSM and then **redeploy** (`cdk deploy --all`) or wait for idle containers to be recycled,
   otherwise warm Lambdas keep the old value.
5. **Deploy order matters** — because engines read SSM at import time, missing parameters
   crash the Lambda at cold start. Create everything in §1 and seed `google_auth.json` (§3)
   **before** the first `cdk deploy --all`.
