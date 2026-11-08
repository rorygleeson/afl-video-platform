# AWS runtime inventory and safe cleanup plan (2026-11-08)

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
