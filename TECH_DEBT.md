# Technical Debt Register

This is the ongoing technical debt register for the AFL Video Platform. Add future findings as separate entries with their risk, dependencies, verification plan and acceptance criteria.

> **Policy:** A debt entry is not permission to delete infrastructure. For live AWS resources, audit actual deployed dependencies, use CloudFormation change sets and verify production functionality before removal.

## TD-001 — Retire legacy mock-video state machine and related infrastructure

- **Status:** Backlog — deliberately deferred; leave existing resources running for now
- **Priority:** Low for immediate functionality; medium for maintainability and avoiding accidental future coupling
- **Recorded:** 2026-11-09
- **Scope:** Legacy Stage 3/4 mock-video workflow, not the real Google video pipeline
- **Decision:** No resource deletions or behaviour changes until a separately reviewed cleanup

### Context

The original Stage 3 mock video workflow is the Step Functions state machine `afl-app-dev-video`, defined in `infrastructure/app/video.yaml`. It produces mock scene results, **not** playable Google-generated MP4s.

Current real-video processing uses:

1. `afl-app-dev-scene1-pilot` — parallel Replicate reference-image generation.
2. `afl-app-dev-google-omni` — real Google video generation, one independent execution per scene.
3. `afl-app-dev-assembly` — final FFmpeg stitching of the three real scene MP4s.

The legacy `afl-app-dev-video` state machine is **not part of this normal end-to-end path**, but the repository and deployment workflows still reference it. It must **not** be manually deleted in AWS now.

### Verified repository dependencies (audit of source, not a live AWS inventory)

| Location | Dependency / concern |
| --- | --- |
| `infrastructure/app/video.yaml` | Defines legacy `afl-app-dev-video` state machine, `afl-app-dev-video-mock-worker` Lambda, IAM roles, logs, and `afl-app-dev-video-admission` DynamoDB table. |
| `src/video_mock_worker/handler.py` | Stage 3 mock video worker implementation. |
| `src/api/handler.py` | `start_mock_video` and mock `POST /jobs/{jobId}/videos/{sceneId}` route remain available. `video_scene_public` and video-status output also support historical mock statuses. |
| `infrastructure/app/backend.yaml` | Has `VideoStateMachineArn` and `VideoAdmissionTableName` parameters, `VIDEO_STATE_MACHINE_ARN` / `VIDEO_ADMISSION_TABLE` / `VIDEO_MODE=MOCK_ONLY` env vars, and IAM rights for mock executions and admission checks. |
| `.github/workflows/deploy-stage4.yml` | **Hard dependency:** explicitly checks the mock CloudFormation stack exists, then supplies its state-machine ARN and admission-table name when deploying the backend. Removing the mock stack first breaks this workflow. |
| `.github/workflows/deploy-video.yml` | Deploys or updates the legacy mock-video stack. Must retire/disable before deletion or it may recreate the stack. |
| `.github/workflows/video-smoke.yml`, `infrastructure/app/video-smoke.yaml` | Stage 3 smoke tests depend on mock state machine and shared admission table; remove or replace their purpose before decommission. |
| `.github/workflows/deploy-google-video.yml` | Legacy **create-only** Google adapter workflow checks that the mock stack exists; assess/retire the workflow rather than breaking an active deployment. |
| `infrastructure/app/google-video.yaml`, `src/google_video_worker/handler.py` | Real Google worker **still declares/references** the admission table and IAM privileges, although global concurrency slot release was retired and real submissions are per-scene. Remove only after checking every current code path and deployed configuration. |
| `src/api/handler.py`, `infrastructure/app/backend.yaml`, `src/video_worker/handler.py` | **Different worker:** `afl-app-dev-video-worker` handles a legacy Google authentication probe and is **not** the same as `afl-app-dev-video-mock-worker`. Audit its auth endpoint, IAM and deployment callers separately; do not inadvertently remove it. |
| `.github/workflows/deploy.yml` | Older full deployment packages the separate auth worker and has parameters that must be reconciled with current backend before any cleanup. |
| `tests/test_video_mock_stage3.py`, `tests/test_video_api_stage4.py`, `tests/test_frontend_poll.cjs` | Mock functionality is covered by tests; update or retire only after API/deployment changes are intentionally reviewed. |

### Risks

- Deleting `afl-app-dev-video` now would break Stage 4 and Stage 3 GitHub workflows and leave stale IAM, API and CloudFormation references.
- The **admission table is provisioned in the mock stack** but referenced by other templates, including the Google worker. Deleting the stack could remove this table and affect legacy or latent runtime paths; confirm it is truly unused in the deployed Google worker before deletion.
- Removing the wrong `video-worker` Lambda could break authentication diagnostics.
- Updating the backend stack from stale templates or workflows could accidentally alter Google paid-video flags, assembly settings, image-worker artefact versions or other unrelated resources.
- CloudFormation stack deletion can remove log groups and historical test data; assess retention and backups.

### Proposed cleanup sequence — not yet authorised

1. **Read-only live inventory:** inspect actual AWS CloudFormation stacks/resources, stack exports/imports, IAM references, running executions, Lambda environment variables, and any CloudFormation drift. Check the table's current access/usage. No changes.
2. **Design changes:** remove mock video submission route and test-only configuration, while preserving real-video read/status APIs, historical mock records if needed, Google scene diagnostics, and finished outputs.
3. **Decouple deployments:** update `deploy-stage4.yml`, retire `deploy-video.yml` and mock smoke workflows, and reconcile older `deploy.yml`/`deploy-google-video.yml` so no remaining workflow requires or recreates the mock stack.
4. **Decouple table safely:** eliminate unnecessary admission-table references and IAM privileges from the **real** Google worker only after proving global slot usage is dead. Preserve per-scene idempotency and paid-request safeguards.
5. **Non-destructive release:** deploy code and template changes without deleting the mock CloudFormation stack. Review the CloudFormation change set for unintended deletes/replacements.
6. **Regression and live verification:** validate reference-image generation, paid video scene permissions and concurrent submissions, no automatic paid retries, read-only Google failure diagnostics, scene MP4 retrieval and final stitching. Prefer existing-job read-only checks and mock tests before any new paid tests.
7. **Explicit deletion gate:** after all references are gone and a rollback plan exists, separately approve deletion of the isolated mock stack through CloudFormation. Verify deletions and application health afterwards. Never delete the shared jobs table, uploads/video bucket, real Google worker or production state machines as part of this task.

### Completion criteria

- No active API path, AWS resource or GitHub Actions workflow references `afl-app-dev-video` or depends on `afl-app-dev-video-admission`.
- Three real-video state machines and their current end-to-end behaviour remain unchanged.
- Failed-scene diagnostics and existing generated MP4s are preserved.
- Relevant tests and deployment jobs pass; deployed AWS resources and stack changes have been independently reviewed.
- Legacy mock infrastructure is removed only after explicit approval and documented verification.

### Audit limitations

This entry reflects repository code/configuration reviewed on 2026-11-09. **Actual AWS stack drift, live table usage, deployed external dependencies and running executions were not independently inventoried.** No zero-risk deletion claim is made.
