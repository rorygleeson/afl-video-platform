# AWS runtime inventory and safe cleanup plan (2026-11-08)

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

This document describes the **CloudFormation templates in Git**, not an independently verified live AWS inventory. Keep paid Google video generation disabled until explicitly approved.

## Core runtime — do not delete

| CloudFormation template | Resource | Purpose |
|---|---|---|
| `infrastructure/app/backend.yaml` | `afl-app-dev-api` | Developer API, job intake, reference image and video status |
| `infrastructure/app/backend.yaml` | `afl-app-dev-image-worker` | Three reference images via Replicate |
| `infrastructure/app/google-video.yaml` | `afl-app-dev-google-omni-worker` | **Only intended production video-generation worker**; paid gate remains hard-disabled |
| `infrastructure/app/google-video.yaml` | `afl-app-dev-google-omni` Step Functions | Isolated future real-video workflow, currently locked |
| `infrastructure/app/video.yaml` | `afl-app-dev-video-mock-worker` and Step Functions | Mock testing regression infrastructure; not exposed in developer UI |

## Legacy/test resources — do not blindly delete

- `afl-app-dev-video-worker` is an **authentication-only Lambda** defined in `backend.yaml`; the current API `GET /video/auth-check` still invokes it via `VIDEO_WORKER_ARN`. Deleting it now would break that endpoint and may break backend CloudFormation updates. Migrate and test the endpoint first; delete only through a reviewed stack update.
- One-off diagnostic templates in `infrastructure/app/google-wif-*.yaml` and `google-preflight-temporary-logs.yaml` may represent separately deployed CloudFormation stacks; their resource names include `afl-app-dev-google-omni-preflight`, `afl-app-dev-google-omni-preflight-v2`, and `afl-app-dev-google-wif-direct-probe`. Live stack existence and ownership have **not yet been checked**. Retain those templates until live inventory and safe stack deletion.
- `infrastructure/app/video-smoke.yaml` defines `afl-app-dev-video-smoke`; retain as disposable test infrastructure pending live resource inventory.

## Repository cleanup completed

Removed seven obsolete, one-off Google WIF investigation/repair GitHub Action **workflows**, preserving Git history. They are not used by any normal application API, image pipeline, or real Google-video workflow. The active `stage5b-google-auth-preflight.yml` remains available for read-only auth regression testing.

## Next safe infrastructure cleanup after first real Scene 1 MP4

1. Confirm deployed CloudFormation stacks and cross-stack references in AWS `ap-southeast-2` (do not infer runtime deployment from Git templates alone).
2. Migrate `GET /video/auth-check` to the isolated Google Omni worker using a least-privilege IAM invocation permission, then run integration checks.
3. Remove the original auth-only Lambda, role and log group from the **backend CloudFormation template** and update the stack via change set. Check exports, backend IAM policies and references before applying.
4. Delete *only confirmed* disposable diagnostic stacks and associated logs through CloudFormation, never by deleting Lambda directly.
5. Retain the mock stack until real-video reliability, idempotency and regression coverage are proven. Remove later by a separately reviewed stack deletion.

No AWS Lambda, IAM role, CloudFormation stack or Google IAM binding was deleted by this repository-only cleanup.


## S3 media retention proposal — 2026-11-16 (not deployed)

Track this work in [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md), findings R5/R8. Current source `infrastructure/app/backend.yaml` expires `uploads/` and `results/` at seven days, while generated `videos/{jobId}/scene*.mp4` and `videos/{jobId}/final.mp4` have no corresponding lifecycle rule. Proposed: clean disposable selfies/reference images/intermediate videos ~24 hours **after verified final assembly**; retain final video seven days initially; define separate exhausted/abandoned-job handling; never delete retry-dependent files, permanent team templates/prompts or the frontend. S3 age-based Lifecycle does not automatically understand a job completion timestamp. Confirm real object inventory and test before any CloudFormation rollout. No cleanup script or lifecycle change has been deployed.
