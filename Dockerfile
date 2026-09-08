# syntax=docker/dockerfile:1

# uv is provided by its dedicated, version-pinned image (no pip bootstrap needed).
FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

# Stage 1: resolve locked dependencies and install them into the Lambda task root.
# Dependencies are exported from the committed uv.lock files (uv export --frozen),
# so builds are deterministic and never re-resolve against indexes at build time.
FROM public.ecr.aws/lambda/python:3.14 AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
COPY --from=uv /uv /uvx /bin/

# Telegram bot Lambda dependencies (lambda/)
WORKDIR /build/lambda
COPY lambda/pyproject.toml lambda/uv.lock ./
RUN uv export --frozen --no-dev --no-editable -o requirements.txt \
    && uv pip install --target "${LAMBDA_TASK_ROOT}" --no-cache -r requirements.txt \
    && rm requirements.txt

# AI engine Lambda dependencies (engines/)
WORKDIR /build/engines
COPY engines/pyproject.toml engines/uv.lock ./
RUN uv export --frozen --no-dev --no-editable -o requirements.txt \
    && uv pip install --target "${LAMBDA_TASK_ROOT}" --no-cache -r requirements.txt \
    && rm requirements.txt

# Stage 2: final Lambda image - dependencies + application code.
# uv and the /build scratch dir stay out of the final image.
FROM public.ecr.aws/lambda/python:3.14
COPY --from=builder "${LAMBDA_TASK_ROOT}" "${LAMBDA_TASK_ROOT}"
COPY lambda/*.py "${LAMBDA_TASK_ROOT}/lambda/"
COPY engines/*.py "${LAMBDA_TASK_ROOT}/engines/"
WORKDIR "${LAMBDA_TASK_ROOT}"
# The Lambda handler is set per function by CDK via the image CMD,
# e.g. lambda.chatbot.telegram_api_handler or engines.gemini.sns_handler.
