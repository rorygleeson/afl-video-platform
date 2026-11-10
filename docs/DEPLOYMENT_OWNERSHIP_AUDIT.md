# Deployment ownership and cleanup audit — 10 November 2026

## Status: READ-ONLY PLANNING; NO AWS DEPLOYMENTS AUTHORIZED

**Do not delete stacks, execute CloudFormation change sets, or remove deployment workflows until the unified release workflow is built and validated.**

## Existing CloudFormation stacks (last live inventory)

| Stack | Intended owner | State |
| --- | --- | --- |
| `afl-app-dev-backend` | Unified release workflow (future) | Existing; currently updated by multiple workflows |
| `afl-app-dev-google-video` | Unified release workflow (future) | Existing; Google Lambda code drift from direct update |
| `afl-app-dev-assembly` | Unified release workflow (future) | Existing; legacy deploy workflow can delete/recreate |
| `afl-app-dev-frontend` | Unified release workflow (future) | Existing infrastructure; frontend object uploads are performed by several workflows |
| `afl-app-dev-video` | Inventory and dependency check before retirement | Existing legacy/mock video stack; do NOT delete without proving no consumers |

The bootstrap stack `afl-video-bootstrap` is a separate foundation stack, not an application release target. Its ownership must be explicitly defined.

## Workflow ownership collisions

| Workflow | AWS writes | Disposition |
| --- | --- | --- |
| `deploy-replicate-identity.yml` | Backend stack, image worker artifact, configuration assets | Retire after replacement |
| `deploy-stage5c-paid-scenes.yml` | Google stack, backend stack, frontend S3/CloudFront, assets | Retire after replacement |
| `deploy-stage6-assembly.yml` | Assembly stack (including delete/recreate branch), backend stack, frontend | Retire after replacement; **high-risk** |
| `release-google-diagnostics.yml` | Direct Lambda code updates (API and Google), frontend | Retire after replacement; **CloudFormation drift source** |
| `publish-scenario-assets.yml` | Scenario prompt/reference assets in S3 only | Keep separate, narrow owner; verify no overlap with unified pipeline |
| `video-smoke.yml` | Creates/deletes disposable `afl-app-dev-video-smoke` stack | Retire or redesign after checking dependencies |
| `preview-google-cfn-reconciliation.yml` | Creates unexecuted CFN change sets; does not execute | Remove after recording/deleting outstanding previews |
| `audit-live-aws.yml` | Read-only | Keep as maintenance audit |
| `identify-live-google-artifact.yml` | Read-only | Temporary; retire after baseline captured |
| `inspect-google-cfn-dependencies.yml` | Read-only | Temporary; retire after baseline captured |
| `diagnose-api-gateway.yml` | Read-only | Keep only if still useful |
| `diagnose-video-executions.yml` | Read-only | Keep only if still useful |
| `verify-aws.yml` | Read-only | Keep as verification if useful |
| `validate.yml` | CI validation | Keep and expand for unified pipeline |
| `validate-google-diagnostics.yml` | CI validation | Merge into primary CI, then retire |

## CloudFormation templates to classify before removal

- Active: `infrastructure/app/backend.yaml`, `google-video.yaml`, `assembly.yaml`, `frontend.yaml`.
- Legacy candidates (NOT yet safe to delete): `video.yaml`, `video-smoke.yaml`.
- Preflight candidates (NOT yet verified unused): `google-preflight-temporary-logs.yaml`, `google-wif-direct-preflight.yaml`, `google-wif-preflight-v2.yaml`, `google-wif-preflight.yaml`.

## Known outstanding risks

1. Google worker live SHA256 is `RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI=`, matching `google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip` in the artifact bucket; CFN currently references an older ZIP. Do not force reconciliation using previously rejected change sets.
2. Two preview change sets were created but not executed: `afl-google-reconcile-42759304709` and `afl-google-reconcile-42762156285`. Confirm current status and delete these **unexecuted previews only**, after preserving audit evidence.
3. Legacy workflows remain manually runnable; concurrency is not resource ownership. Block/remove old writers only when a validated replacement is ready, or explicitly approve a deployment freeze.
4. The shared GitHub AWS deploy role can perform broad writes. After pipeline migration, reduce permissions to enforce ownership, not just conventions.
5. Check all cross-stack outputs, API/Lambda/Step Functions ARNs, IAM references, S3 prefixes and CloudFront ownership before retiring stacks or templates.

## Required exit gates

1. Freeze application deployments during migration (explicit approval).
2. Capture exact current AWS resource inventory, CFN stack parameters and deployed artifact hashes, with rollback references.
3. Define one authoritative workflow for each active stack and application Lambda/frontend release; scenario-assets publisher owns only scenario asset keys.
4. Build unified release with immutable artifacts, one concurrency group, drift-aware preflight, change-set preview, explicit human approval, safe apply, post-deploy verification and rollback instructions.
5. Validate pipeline in no-write mode, and verify no unexpected replacements or deletions.
6. With approval, perform a controlled deployment, confirm hashes and functional health, then remove superseded writers and temporary diagnostic YAML.
7. Re-run repository ownership audit: **no two active workflows may write the same stack, Lambda code, frontend key or asset prefix**.

No production resources were changed by creating this audit document.
