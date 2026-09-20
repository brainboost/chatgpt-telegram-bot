# ADR-0001: Provider vocabulary — no blanket rename of "engine"

Status: Accepted · Date: 2026-09-08 · Applies to: architecture worktree (`architecture` branch)

## Context

The project started in 2022 as a free-tier Telegram bot routing chat requests
to frontier-model chat APIs. Those endpoints were nicknamed "**engines**" —
a misnomer: there is no inference engine inside; each one is a thin bot-side
worker that adapts an external provider API, keeps a small per-user session
and publishes results.

The word *engine* has since drifted to mean the *identity of a selectable
backend capability in the fan-out* and carries several roles at once:

- SNS **MessageAttribute** name `engines` + CDK `SubscriptionFilter` policies
  (`stacks/engines_stack.py`),
- **stored user preference** key in DynamoDB `user-configurations`
  (`lambda/user_config.py`) and the `/engines` command,
- **context scope** key (`engine` attribute on `user-context` rows),
- **runtime identity** (`responder.label`) and the **user-visible**
  `*__<id>__*` reply header,
- env `OLLAMA_ENGINE`, the `engines/` bundle and `engines_stack.py`.

A rename to "provider" alone would not fix the conflation: *provider* names the
external API (Ollama Cloud, DeepL, Ideogram, Gemini), while the bot-side unit is
a worker around it. Fully accurate vocabulary would need two terms, and the
wire/storage compatibility surface (SNS attribute, persisted user config,
DynamoDB keys, env vars) makes a blanket string rename a cross-deploy churn
with message-drop risk and zero behavioral value.

## Decision

- Adopt **provider** as the domain term for a selectable backend capability
  on all *new* surfaces: new modules, docstrings, docs and `CONTEXT.md`.
- Do **not** blanket-rename "engine" across the repo. The following legacy
  seams keep the word engine as their stable wire/storage identity:
  SNS attribute `engines` + CDK filter policies, DynamoDB
  `user-configurations."engines"`, result payload `engine`, `user-context`
  `engine` attribute, `OLLAMA_ENGINE`, bundle/infra names.
- Centralize provider identity as data (provider catalog) before any further
  vocabulary change; the catalog is the vehicle if the term ever migrates.

## Consequences

- New readers are not misled: *provider* describes the real concept, and the
  glossary records that an "engine" is the legacy word for a provider id on
  the wire/storage.
- No behavior change and no risky cross-seam migration.
- Future architecture reviews should not re-suggest a blanket rename; a staged
  migration of the wire attribute and stored keys would require dual-write and
  lockstep deploys and should be justified on its own.
