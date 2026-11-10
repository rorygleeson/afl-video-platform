# AFL Video Platform — Production-Ready Pipeline Migration Plan

**Status:** AUDIT COMPLETED; RELEASE PIPELINE IMPLEMENTATION PENDING — see execution reset below  
**Audit date:** 2026-11-10  
**Scope:** Existing single AWS environment, `ap-southeast-2`; GitHub repository `rorygleeson/afl-video-platform`  
**Authority:** This document is the implementation checklist. Verify live AWS facts before any cutover.

## Execution reset — 10 November 2026 (authoritative next steps)

**Objective:** production-quality IaC for the *existing single AWS environment*, with no competing writers, no unexplained drift, and a clean repository. This section supersedes earlier speculative Google reconciliation attempts. Do not repeat the failed previews.

**Evidence already gathered:** Five application CFN stacks and five Lambdas inventoried; Google live ZIP SHA verified; deployed Google CFN template byte-identical to GitHub; two Google change-set previews blocked (one proposed permission replacement). Current application is working. Previous experimental Google template edit was reverted.

### Milestones and acceptance gates

| Order | Deliverable | Acceptance criteria | AWS effect |
| --- | --- | --- | --- |
| 1 | **Freeze and baseline** | Agree deployment freeze; record all five stack IDs/parameters, Lambda live hashes, Step Functions, frontend object hashes, exact ZIPs and rollback manifest. Inventory unexecuted change sets. | Read-only until explicitly approved |
| 2 | **Single-writer architecture** | One `release.yml` owns all application CFN stack updates, Lambda code and frontend publication. `publish-scenario-assets.yml` owns only scenario keys. Bootstrap remains separately controlled. Document exact S3 prefix boundaries. | Repo only |
| 3 | **Build release tooling** | Deterministic packages, SHA256 manifest, immutable S3 keys, pinned dependencies/actions, least-privilege OIDC, explicit CFN parameter preservation, ordered stack change sets, drift checks, deletion/replacement blocks, manual approval and verification. No direct `update_function_code`. | Repo/CI only |
| 4 | **Dry-run validation** | CI tests and a read-only/preview release against the actual live baseline. All proposed resource changes explained; no unexpected replacements, deletes, or Google worker downgrade. Preview may create change sets only with approval and must delete its own unexecuted change sets. | No live app changes |
| 5 | **Approved cutover** | Human-approved first controlled deployment; verify deployed hashes, API health, Step Functions, frontend and failure/rollback path. Paid video test only by separate approval. | AWS changes only after approval |
| 6 | **Remove overlap and leftovers** | Disable/delete the four competing application writers, remove temporary diagnostic workflows and unreferenced preflight/mock templates after proving no dependencies; safely delete old unexecuted change sets; check GitHub workflows and AWS permissions for a single writer. | GitHub cleanup; AWS stack deletion separately approved |
| 7 | **Production-quality sign-off** | Documented release and rollback runbook, CI green, no unauthorized writers, no known unmanaged code drift, no stale unsafe workflows, auditable release history. | Verification |

### Mandatory engineering rules

- **No deployment until milestone 4 passes and the user explicitly approves milestone 5.**
- Never delete/recreate `afl-app-dev-assembly` as a recovery tactic.
- Never run the rejected change sets `afl-google-reconcile-42759304709` or `afl-google-reconcile-42762156285`.
- Do not resolve Google drift by downgrading to its older CFN S3 key. The verified live ZIP is the baseline. Choose a controlled, reviewable migration strategy in the release design.
- Do not assume a shared concurrency group solves ownership. Prevent other writers by disabling legacy workflows at cutover and narrowing IAM permissions.
- No dev/prod split; no paid video generation in CI; no speculative one-off diagnostic workflows.
- No deleting existing CFN stacks, templates, or data until all references and rollback needs are verified.

### Current progress and immediate work queue

- [x] Repository and AWS stack inventory
- [x] Identify Google live code ZIP and its hash
- [x] Confirm deployed Google template matches GitHub
- [x] Revert unsafe Google template experiment
- [x] Document workflow collisions and template cleanup candidates
- [ ] Baseline frontend and all live release artifacts in a machine-readable manifest
- [ ] Implement `release.yml` and supporting build/preview/verify scripts
- [ ] Add tests proving no other active workflow can write application stacks, Lambdas, or frontend
- [ ] Execute safe dry-run and capture evidence
- [ ] Obtain approval for first deployment and verify
- [ ] Remove obsolete workflows/templates and reduce IAM permissions

**Progress reporting:** Every implementation update must state exact files/commits, tests run, outstanding blockers and whether AWS was changed. Do not claim production readiness from a green workflow alone.

---

## 0. Non-negotiable safeguards

1. Preserve the working application, its data, API contracts, frontend, Google/Replicate integration and existing paid-video controls.
2. **One environment only**. Do not introduce dev/prod separation in this project phase.
3. Steps 1–5 are repository analysis, documentation, local/CI validation and **read-only AWS inspection** only. No CloudFormation execution, Lambda code updates, stack deletion, frontend publication or paid video submission without separate explicit approval.
4. Live AWS is the source of truth for what is currently running. GitHub templates and CloudFormation parameter values are **not proof** of live Lambda code or configuration.
5. One authoritative release path per CloudFormation stack; one release coordinator may sequence multiple stacks. No independent feature workflow may replace a shared API package.
6. Immutable artifacts, reproducible build inputs, pinned dependencies, recorded checksums, change-set review, explicit rollback assets and post-deploy verification.
7. No automatic paid model requests during release verification. Paid requests require a separately approved test.
8. Do not delete legacy code, templates, workflows or stacks until dependencies and rollback are verified.
9. Keep each change small and reversible; record commit, test evidence and verification outcome here. A green CI run is not proof of a safe live deployment.

## 1. Repository audit — completed 2026-11-10 (live AWS audit still pending)

### 1.1 Confirmed GitHub workflow inventory (11 workflows)

| Workflow | Trigger | Observed effect | Decision |
|---|---|---|---|
| `deploy-replicate-identity.yml` | Manual | Publishes image prompts and image worker ZIP; updates **backend** CloudFormation | Replace overlapping backend deployment |
| `deploy-stage5c-paid-scenes.yml` | Manual | Deploys **Google** and **backend** stacks, publishes video prompts and frontend; health/auth checks | Migrate to orchestrated release |
| `deploy-stage6-assembly.yml` | Manual | Deploys **assembly** and **backend** stacks, publishes frontend; includes assembly delete/recreate branch | Remove unsafe recovery path before use |
| `release-google-diagnostics.yml` | Manual | Directly updates Google and API Lambda code; publishes frontend; creates but does not execute CFN change sets | Retire only after live drift reconciliation and replacement proven |
| `publish-scenario-assets.yml` | Push on scenario assets + manual | Publishes S3 scenario configs/prompts and optional PNGs, verifies bytes | Retain initially; define asset ownership and versioning |
| `validate.yml` | Push to main + PR | General syntax, CloudFormation and unit tests | Retain as core CI |
| `validate-google-diagnostics.yml` | Selected push paths + manual | Diagnostics contract assertions | Preserve tests; may fold into core CI later |
| `diagnose-api-gateway.yml` | Workflow-file push + manual | Read-only API/stack diagnosis | Retain as operational tool; review auto trigger |
| `diagnose-video-executions.yml` | Workflow-file push + manual | Read-only video execution diagnosis | Retain as operational tool; review auto trigger |
| `verify-aws.yml` | Manual | Read-only OIDC caller identity check | Retain or fold into read-only preflight |
| `video-smoke.yml` | Manual | Creates and deletes a disposable **mock smoke** stack | Legacy candidate; do not run during migration |

**Finding F1 — competing backend writers:** Replicate, Stage 5C and Stage 6 each call `aws cloudformation deploy` on `afl-app-dev-backend`. Stage 5C and Stage 6 both update `LambdaArtifactKey`, so later releases can replace the API code from another release. Their shared GitHub concurrency group serializes executions but does not prevent semantic rollback.

**Finding F2 — direct Lambda drift:** `release-google-diagnostics.yml` uses boto3 `update_function_code` for both Google and API Lambdas, bypassing CloudFormation. Its CloudFormation change sets are inspection-only and are deleted without execution. Current live drift is **UNKNOWN**.

**Finding F3 — assembly stack deletion:** `deploy-stage6-assembly.yml` contains `aws cloudformation delete-stack` and `stack-delete-complete` before deployment under a conditional stack-state branch. This must not be treated as a normal recovery strategy.

**Finding F4 — frontend multiple writers:** Stage 5C, Stage 6 and diagnostics release independently publish `index.html`, `app.js` and `styles.css` to the frontend bucket. Frontend ownership must be unified.

**Finding F5 — asset multiple writers:** Stage 5C and `publish-scenario-assets.yml` both publish video prompts. The old manual prompt-sync workflow was removed in commit `22d7add`. Define whether scenario assets are independently published or versioned as part of a release.

**Finding F6 — drift guard exists but has not run against AWS:** Stage 5C contains a pre-deploy check comparing live Lambda `CodeSha256` with the CloudFormation-recorded S3 artifact SHA256 for Google and API. Commit `937bbd5`. This guard blocks on mismatch; it does not reconcile drift or establish a rollback baseline. Its AWS permissions and behaviour require verification.

### 1.2 CloudFormation template and resource ownership (repository evidence)

| Template | Known resources / role | Ownership notes |
|---|---|---|
| `infrastructure/bootstrap/bootstrap.yaml` | Bootstrap artifact S3 bucket, GitHub OIDC deploy role, CloudFormation execution role, permissions boundary policy | One-time bootstrap; never recreate as part of normal release |
| `infrastructure/app/backend.yaml` | API Lambda, image worker, legacy auth-only video worker, backend API/storage/orchestration resources | Backend stack: `afl-app-dev-backend`; shared by several feature workflows |
| `infrastructure/app/google-video.yaml` | Google Omni Lambda, IAM role, Google video Step Functions | Google stack: `afl-app-dev-google-video` |
| `infrastructure/app/assembly.yaml` | Assembly Lambda, FFmpeg layer, assembly Step Functions, IAM | Assembly stack: `afl-app-dev-assembly` |
| `infrastructure/app/frontend.yaml` | Private frontend S3, CloudFront, OAC and bucket policy | Frontend stack: `afl-app-dev-frontend` |
| `infrastructure/app/video.yaml` | Mock video worker and state machine | Legacy/mock candidate; **live deployment status unverified** |
| `infrastructure/app/video-smoke.yaml` | Disposable smoke-test resources | Test-only; not a core release stack |
| `infrastructure/app/google-*preflight*.yaml` | Historic Google WIF/preflight variants | Legacy candidates; do not delete until usage and deployed stacks checked |

The table records **template definitions and workflow references**, not an independently verified AWS inventory. A stack may have changed outside GitHub. Resource names and cross-stack wiring must be checked against AWS.

### 1.3 Known source and test locations

- API: `src/api/handler.py`, `src/api/video_prompts.py`
- Reference images: `src/image_worker/handler.py`
- Google paid video and diagnostics: `src/google_video_worker/handler.py`
- Legacy auth-only worker: `src/video_worker/handler.py`
- Final assembly: `src/assembly_worker/handler.py`
- Frontend: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`
- Scenario config/prompts: `scenarios/afl-grand-final-winning-kick/v1/`
- Tests: `tests/`; existing general CI: `.github/workflows/validate.yml`

### 1.4 Audit limitations and open questions

- **NOT YET VERIFIED IN AWS:** Actual stack inventory and states, live Lambda hashes and runtime config, S3 artifact availability/versioning, Step Functions definitions, frontend deployed asset hashes, IAM permissions, alarms, API Gateway stages, or stack drift.
- Determine whether the mock `afl-app-dev-video` state machine is still referenced by backend configuration before touching it.
- Verify assembly stack existence and current state before changing its workflow.
- Verify whether GitHub's recorded artifact keys correspond to running Lambda packages. Do not infer from CloudFormation parameters alone.
- Confirm whether current S3 prompt objects are consumed by in-flight jobs and whether publication needs immutable per-release keys.
- Review deployment role's permissions for read-only preflight versus write operations; enforce least privilege without breaking current access.

## 1.5 First live AWS inventory — 2026-11-10 (workflow run #1)

**Evidence:** [GitHub Actions run 42743816772](https://github.com/rorygleeson/afl-video-platform/actions/runs/42743816772), job 129591031514, user-provided log. This run **failed intentionally** after detecting a Google Lambda artifact mismatch. The application was not deployed or changed by this inventory.

| Component | Live finding | Interpretation |
|---|---|---|
| Backend stack | Reported present; full resource listing AccessDenied | Stack metadata only |
| Google stack | Reported present; full resource listing AccessDenied | Stack metadata only |
| Assembly stack | UPDATE_COMPLETE; full resource listing AccessDenied | Stack metadata only |
| Frontend stack | CREATE_COMPLETE; full resource listing AccessDenied | Stack metadata only |
| Mock-video stack | CREATE_COMPLETE; full resource listing AccessDenied | **Confirmed deployed**, not safe to delete without dependency review |
| API Lambda | Active; code hash **MATCH** against recorded S3 artifact | Verified code/artifact consistency at audit time |
| Google Omni Lambda | Active; code hash **DRIFT** from recorded S3 artifact | **Critical: do not redeploy old CloudFormation artifact**; preserve live code |
| Image Lambda | AccessDeniedException | No conclusion about existence or state |
| Legacy video Lambda | AccessDeniedException | No conclusion about existence or state |
| Assembly Lambda | AccessDeniedException | No conclusion about existence or state |
| Step Functions | AccessDeniedException | No conclusion about live state machines |

**Root cause of partial inventory:** The current GitHub OIDC deployment role's inline policy allows `cloudformation:DescribeStacks` but not `cloudformation:ListStackResources`, `lambda:GetFunctionConfiguration` for all worker functions, or `states:ListStateMachines`. These are authorization limitations, not proof of missing resources. The audit uses the existing deployment role, which has write capabilities, but its **script only makes read calls**. Future design should prefer a dedicated read-only audit role; do not modify IAM/bootstrap without separate approval.

**Important:** The Google worker mismatch may reflect a legitimate direct diagnostics deployment. The live code hash and working version must be preserved before reconciling CloudFormation's recorded artifact key. No automatic overwrite, no blind re-release, and no destructive stack recovery.

**Step 1 remains partially open:** Live stack metadata and two Lambda comparisons verified; full resource inventory blocked by IAM. Step 2 baseline is **not yet satisfied**.

## 1.6 Second live inventory — 2026-11-10 (after bootstrap IAM update)

**Evidence:** [Audit run #2](https://github.com/rorygleeson/afl-video-platform/actions/runs/42749371264), job 129609082082; complete GitHub Actions log inspected. Bootstrap stack was updated successfully to `UPDATE_COMPLETE`, adding only the three previously reviewed read-only IAM permissions. The inventory is now complete for the five configured stacks, five configured Lambdas and Step Functions listing.

| Stack | Status | Listed resources |
|---|---|---:|
| afl-app-dev-backend | UPDATE_COMPLETE | 27 |
| afl-app-dev-google-video | UPDATE_COMPLETE | 6 |
| afl-app-dev-assembly | UPDATE_COMPLETE | 6 |
| afl-app-dev-frontend | CREATE_COMPLETE | 4 |
| afl-app-dev-video (mock) | CREATE_COMPLETE | 6 |

| Lambda | State | Live SHA vs recorded artifact |
|---|---|---|
| afl-app-dev-api | Active | MATCH |
| afl-app-dev-image-worker | Active | MATCH |
| afl-app-dev-video-worker (legacy) | Active | MATCH |
| afl-app-dev-google-omni-worker | Active | **DRIFT** |
| afl-app-dev-assembly | Active | MATCH |

Four STANDARD state machines listed: `afl-app-dev-assembly`, `afl-app-dev-google-omni`, `afl-app-dev-scene1-pilot`, `afl-app-dev-video`. Presence does not prove active use. No AWS application changes made by the inventory.

**Outstanding hard gate:** Preserve and verify the **live Google worker code** (SHA256 `RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI=`) before any Google stack deployment. The stack's current S3 artifact differs; do not overwrite the live worker with the recorded artifact. Identify which direct diagnostics release created the current live version, capture an immutable recoverable copy, and reconcile IaC via a reviewed change set. Inspect dependencies before retiring mock/pilot resources. Step 1 inventory substantially complete; Step 2 immutable rollback baseline **not complete**.

## 1.7 Live Google package identified — 2026-11-10

**Evidence:** [Successful read-only provenance run #2](https://github.com/rorygleeson/afl-video-platform/actions/runs/42753399499), job 129622051966. Compared 59 existing ZIP objects against live Google Lambda SHA256. Exactly one matched:

- Live Google function: `afl-app-dev-google-omni-worker`
- **Matching S3 artifact:** `s3://afl-video-bootstrap-artifactbucket-xiuriidgyft4/google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip` (8,287,174 bytes)
- Live Lambda SHA256: `RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI=` (exact ZIP digest match)
- **Currently recorded CFN parameter:** `GoogleWorkerArtifactKey=google-video/bae89b65942bfabd9fac270af811e01e08e429ea.zip` (mismatch)

This strongly supports direct diagnostics code deployment as the source of drift. **The correct live ZIP already exists in S3, but object immutability, retention and separate recovery copy have not yet been verified.** No automatic CloudFormation correction or Lambda redeploy authorized. Next: inspect template, permissions, retention, and generate a reviewed Google-stack change set that points to the verified live ZIP, with a hard gate against other resource changes.

## 2. Target release architecture

```text
GitHub main / reviewed commit
        |
        v
CI: syntax + unit/contract + CloudFormation validation
        |
        v
Build ONCE: API, image worker, Google worker, assembly worker/layer, frontend
        |
        v
Immutable versioned artifacts + SHA256 release manifest
        |
        v
Single manual release orchestrator (one AWS environment)
        |
        +--> preflight: read-only live baseline + drift checks
        +--> CloudFormation change-set previews (NO execution yet)
        +--> explicit review/approval of proposed changes
        +--> dependency-ordered stack deployments:
        |      Google stack owner -> Assembly stack owner -> Backend stack owner
        |      (only stacks actually changed; ordering verified against dependencies)
        +--> frontend asset publication (one owner, after API verification)
        +--> post-deploy non-paid health checks + release record
```

**Ownership contract:** One implementation entry point per stack; a single orchestrator invokes those entry points. Google/Assembly/Replicate feature workflows cannot deploy the backend themselves. All backend changes (API, image worker, feature flags) are assembled into **one coherent backend release**. Separate stack ownership does not require separate independently triggered GitHub workflows.

**Deployment ordering is provisional** until the real Step Functions, API and cross-stack references are mapped. No automatic rollback across external paid requests; preserve job idempotency and reconcile partial deployments explicitly.

## 3. Execution plan and hard gates

### Step 1 — Repository audit and plan [REPOSITORY AUDIT COMPLETE; LIVE VERIFICATION OPEN]

- [x] Inventory all 11 current workflows, their triggers and AWS write operations.
- [x] Identify major CloudFormation templates, workers and overlapping deployment ownership.
- [x] Record specific risks and create this plan.
- [ ] Complete read-only live AWS inventory and verify differences against repository.
- **Gate:** No claim of a fully verified architecture until live AWS inventory is attached to this document.

### Step 2 — Snapshot and protect working application [PENDING]

- [ ] Read-only capture of stack names, statuses, templates, parameters, outputs, resource IDs and drift status.
- [ ] Record each Lambda's `CodeSha256`, runtime, environment variable **names** (never secrets), function version/config and referenced S3 object.
- [ ] Record deployed Step Functions definitions/ARNs, API Gateway configuration, S3 artifact versions and frontend file hashes/CloudFront distribution.
- [ ] Confirm that referenced artifacts are accessible; preserve exact live packages and recovery manifest securely. Do not overwrite existing keys.
- [ ] Identify direct-update drift; propose reconciliation without executing it.
- **Gate:** Exact known-good release and tested restoration procedure are documented before any deployment.

### Step 3 — Define single-writer stack interfaces [PENDING]

- [ ] Produce stack-by-stack resource ownership matrix with cross-stack inputs/outputs.
- [ ] Define release manifest: commit SHA, template revisions, artifacts, hashes, current parameter values, expected changes and rollback references.
- [ ] Centralize backend deployment; isolate Google and Assembly deployment steps without duplicated backend writes.
- [ ] Define frontend and scenario-asset publishing ownership.
- [ ] Preserve current API routes, paid gating, Step Functions and feature settings.
- **Gate:** Every deployed resource has exactly one release owner; no functionality is dropped.

### Step 4 — Reproducible build and artifact pipeline [PENDING]

- [ ] Pin Python and JavaScript build tools/dependencies and native-wheel architecture.
- [ ] Package each Lambda/layer separately with deterministic inputs; generate hashes and artifact manifest.
- [ ] Upload immutable artifacts without overwriting existing objects; verify object hashes.
- [ ] Run existing tests plus diagnostics, API, reference-image, video and assembly contract tests.
- **Gate:** Same source and pinned dependencies produce an independently verifiable release; no AWS runtime updates.

### Step 5 — Safe release implementation and preview [PENDING]

- [ ] Implement a single manual release orchestrator; disable automatic application deployments.
- [ ] Preflight live Lambda hashes, stack states, parameters and critical resource ownership.
- [ ] Create **non-executed** CloudFormation change sets for each affected stack; enumerate Add/Modify/Remove and replacement flags.
- [ ] Fail closed on unexpected deletion/replacement, parameter resets, missing artifacts, unresolved drift or unsupported stack state.
- [ ] Require explicit approval before executing any change set.
- [ ] Remove automatic assembly delete/recreate behaviour from replacement path.
- [ ] Provide release record and rollback procedure for each stack and frontend.
- **Gate:** All previews and policy checks pass; **no live deployment has yet occurred**.

### Step 6 — Prove replacement path [PENDING — SEPARATE APPROVAL REQUIRED]

- [ ] Review exact change sets and known-good recovery manifest.
- [ ] Execute only an explicitly approved minimal deployment; observe stack events and post-deploy non-paid health checks.
- [ ] Verify frontend, API, reference-image controls, diagnostics, paid-video controls and assembly contracts.
- [ ] Require separate authorization before any paid provider generation test.
- [ ] Validate recovery/rollback using approved safe method; do not destroy live stacks.
- **Gate:** Working app remains functional; deployed artifacts match manifest; rollback is demonstrably available.

### Step 7 — Retire competing deployment paths [PENDING]

- [ ] Only after Step 6, disable and remove direct Lambda diagnostics release.
- [ ] Remove independent backend deployments from Replicate/Stage 5C/Stage 6 feature workflows.
- [ ] Retire obsolete mock/preflight/smoke workflows only after verifying they are unused.
- [ ] Update outdated `docs/DEPLOYMENT.md`, `docs/RESOURCE_CLEANUP.md` and architecture docs to avoid misleading runbooks.
- **Gate:** One deployment owner per stack and no bypass via direct Lambda code update.

### Step 8 — Production reliability hardening (same environment) [PENDING]

- [ ] Audit retries, timeouts, Step Functions transitions, idempotency, duplicate paid submissions and reconciliation of uncertain Google responses.
- [ ] Audit job state persistence, failure diagnostics, payload size limits, stuck-job recovery and observability.
- [ ] Review least-privilege IAM, secrets handling, cost guardrails, monitoring, alarms and operational runbooks.
- [ ] Verify end-to-end failure recovery without triggering unapproved paid requests.
- **Gate:** Operational failure modes are tested and documented before onboarding paying customers.

## 4. Change control and rollback requirements

For every change, record: affected files, rationale, affected stacks, AWS write operations (if any), pre-change live hashes, test results, change-set diff, approval, commit SHA, deployed artifact keys, post-deploy checks and rollback instructions.

**Stop conditions:** Unexplained drift; missing known-good artifact; replacement/deletion in an unexpected change set; changed IAM/security boundaries; disabled paid gate; unexpected frontend/API changes; unavailable rollback; failed non-paid health check. Stop and investigate, do not retry a destructive operation blindly.

CloudFormation stack rollback is not equivalent to reverting Lambda code updated out of band, S3 frontend assets, in-flight Step Functions jobs, provider video requests or customer payments. Each requires its own documented recovery strategy.

## 5. Progress log

| Date | Step | Status | Evidence |
|---|---|---|---|
| 2026-11-10 | Existing pipeline cleanup | Complete for previously verified commits | 17 workflows reduced to 11; latest user-shared GitHub Actions screenshot shows green CI for commits `22d7add` and `2efb78f` |
| 2026-11-10 | Step 1: repository audit | Complete | Repository workflow and CloudFormation template inspection |
| 2026-11-10 | Step 1: live AWS inventory | **Pending** | No live AWS read-only inspection performed |
| 2026-11-10 | Steps 2–8 | **Not started** | No live deployment authorized |

## 6. Next action

Perform **read-only live AWS inventory** (Step 1 completion / Step 2 baseline) through an approved, manual GitHub Actions inspection or equivalent authorized AWS read access. Capture sanitized outputs and compare them with this document. **Do not deploy or delete anything.** If access is unavailable, mark the gate blocked rather than guessing.

After each accepted milestone, update this document and link the specific commit and CI evidence. Never mark a milestone complete based solely on a successful GitHub commit.
