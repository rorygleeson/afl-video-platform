# Deployment baseline audit — 15 November 2026

**Audit checkpoint:** Task 1.1 of [RESILIENCY_ROADMAP.md](RESILIENCY_ROADMAP.md).  
**Mode:** Read-only source inspection. **No application code, infrastructure, providers or payments changed.**  
**Verification status:** **INCOMPLETE** — repository source inspected; direct live AWS access and deployment run artifacts unavailable through this connection.

## Source baseline

- Repository: `rorygleeson/afl-video-platform` (private); default branch `main`.
- Last application/prompt commit observed before the roadmap: `57c8ecb5d892e0a6edf77f242d47b1747065191b` — “Tighten one-ball continuity constraint in Scene 2 only.”
- Roadmap commit: `a8598e0f375a9eed06805c48452b2970e66d58f1`.
- Important recent deployment-intent commits: `0cf817fdfcb1a83ce1f1a46005098db5994172c6` (sequential image workflow), `3f1872de23c260c3a684c33eafd46d7054946797` (stitching progress UI). **A commit message is not live deployment evidence.**
- AWS region configured in deployment tooling: `ap-southeast-2`.
- Current repository documentation `docs/ARCHITECTURE.md` / `docs/API_ENDPOINTS.md` dated 8 November describes obsolete Replicate, parallel images and missing assembly. Update only after verifying current implementation.

## Discovered live-baseline tooling

`scripts/capture_release_baseline.py` is designed to run with an authorised AWS role and perform read-only operations. It collects:

- CloudFormation status, identity, parameters, resource inventory and exact deployed template hashes for `afl-app-dev-backend`, `afl-app-dev-google-video`, `afl-app-dev-assembly`, `afl-app-dev-frontend`, `afl-app-dev-video`.
- Lambda runtime, version, status and `CodeSha256` for `afl-app-dev-api`, `afl-app-dev-image-worker`, `afl-app-dev-video-worker`, `afl-app-dev-google-omni-worker`, `afl-app-dev-assembly`.
- State machine identity, state, definition SHA256 and role when `states:DescribeStateMachine` is allowed.
- Google worker's deployed artifact integrity (actual Lambda code SHA256 versus active CloudFormation artifact) and live timeout equals 300 seconds, or fails closed.
- `release-baseline.json` and retrieved deployed templates as workflow artifacts. It is **not** a comprehensive deployment inventory by itself.

`.github/workflows/release.yml` includes a baseline job assuming the GitHub OIDC AWS deploy role, creating release artifacts, calling `scripts/capture_release_baseline.py` and read-only `scripts/verify_release_health.py`. Other workflow jobs have explicit deployment triggers. **Do not dispatch deploy-capable workflows for the audit.** `scripts/check_deployment_ownership.py` exists to check competing deployment writers.

**Limitations of this inspection:** GitHub connector returned no associated PR-triggered workflow runs for roadmap commit; this does *not* mean no push-triggered workflows executed. The connector does not expose recent GitHub Actions run logs/artifacts or AWS boto3 access from this conversation. No live baseline JSON was retrieved. Therefore source CloudFormation does **not** establish actual deployed values.

## Source template inventory (NOT verified live)

| Resource | Source configuration |
|---|---|
| REST API | `afl-app-dev-api`, greedy `ANY /{proxy+}` AWS_PROXY with API key; CORS OPTIONS mock |
| API Lambda | `afl-app-dev-api`, Python 3.13, 256 MB, 15 s |
| Image Lambda | `afl-app-dev-image-worker`, Python 3.13, 1024 MB, 600 s |
| Image workflow | `afl-app-dev-scene1-pilot` STANDARD, 1800 s; Map max concurrency 1 |
| Image watchdog | `afl-app-dev-reconcile-images`, EventBridge every 5 minutes |
| Google video Lambda | `afl-app-dev-google-omni-worker`, Python 3.13, 1024 MB, 300 s |
| Google video workflow | `afl-app-dev-google-omni` STANDARD, 3600 s; 20 s poll wait; PollVideo TaskFailed retry 3, 10-second base, backoff ×2 |
| Assembly Lambda | `afl-app-dev-assembly`, Python 3.13, 2048 MB, 600 s, 2048 MB ephemeral |
| Assembly workflow | `afl-app-dev-assembly` STANDARD; one Stitch task, no workflow Retry |
| Job table | `afl-app-dev-jobs`, partition key `jobId`, TTL field `expiresAt` |
| Upload/results bucket | `afl-app-dev-uploads-${AWS::AccountId}-${AWS::Region}`; source lifecycle `uploads/` and `results/` expire in 7 days |
| Google image endpoint | Vertex AI `gemini-3-pro-image:generateContent`, global, direct Google WIF |
| Google video endpoint | Vertex AI Interactions API, `gemini-omni-1.2-flash` |
| Frontend | S3 + CloudFront private origin |

**Important:** API source still emits `parallelScenes: true` in `GET /scenarios`, despite source image workflow Map concurrency one. `start_images` also has obsolete parallel/Replicate wording. These are documentation/API accuracy defects, not proof of the deployed path.

## Additional checks required to complete Task 1.1

- [ ] Obtain a **fresh, non-deploying** OIDC-authorised release baseline capture, with workflow result, commit SHA, collection time and restricted artifact reference. Do not publish raw credentials, customer data or potentially sensitive CloudFormation parameters.
- [ ] Compare each live CloudFormation template/parameters to source; explicitly detect drift, replacements and externally patched Lambdas.
- [ ] Read actual Lambda settings: timeouts, memory, ephemeral disk, reserved concurrency, retry/event configuration, environment variable *names* only, dependency package checksums, and function code hashes.
- [ ] Read actual Step Functions definitions, state transitions, execution timeouts/retries, EventBridge delivery configurations, and API Gateway stage/integration/usage-plan settings.
- [ ] Inventory DynamoDB TTL and PITR, S3 lifecycle/encryption/access, CloudFront, logs and alarms, schedules, operational budgets, and current secret references without values.
- [ ] Check last successful deployments of backend, Google, assembly, frontend, assets; link evidence to exact artifact SHA/stack revision. Inspect GitHub Actions status through the Actions UI or an authorised connector capable of reading push-triggered workflow runs.
- [ ] Reconcile observed deployment with source and tag every documented setting as **SOURCE VERIFIED**, **LIVE VERIFIED**, or **UNVERIFIED**.
- [ ] Extend baseline reporting where needed with additional **read-only** checks, and mock-test those changes. Review workflow permissions before any run.

## Baseline security and execution note

The existing baseline script includes CloudFormation parameter values in its generated JSON. **Treat baseline artifacts as restricted**; do not commit them verbatim to GitHub or paste their contents into public notes. Documentation should store masked and relevant nonsecret metadata, stable resource identifiers, config values and integrity hashes only. A GitHub Action with AWS access should run a read-only audit path; a deployment-capable workflow must never be used without reviewing all triggers and commands.

## Findings and decision

- **SOURCE VERIFIED:** current repo contains staged image/video/assembly workflows and a read-only live-baseline implementation.
- **SOURCE VERIFIED:** repository documentation and some response strings are stale.
- **UNVERIFIED:** which artifact versions, template properties, timeouts, environment flags and execution definitions are actually running in AWS.
- **OPEN:** Step 1.1 remains **in progress** until live evidence is available and reconciled. Do **not** mark complete or begin runtime hardening based only on source templates.

**Next:** safely retrieve and compare AWS live baseline evidence, then perform Steps 1.2–1.9 document updates from verified source and deployment facts.
