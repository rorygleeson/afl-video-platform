# Documentation quality review — 16 November 2026

**Scope:** Documentation inventory, API contract, job-status lifecycle, retries/timeouts, Google video workflow, FFmpeg assembly, EventBridge/watchdog, media retention, source-vs-release history. **Mode: source-only repository inspection; documentation and offline static validation only.** No AWS deployment, customer media inspection, payment change or Google model submission authorised.

## Overall assessment

The repository had useful detailed notes but lacked a single clear hierarchy: old rollout checklists and newer current references both described outdated retry and status behaviour. A developer reading the first page of several “current” docs could get an incorrect answer even when a later appended paragraph was right. The correction was to **replace** the two most critical contracts, establish a clear documentation home and explicitly classify historical pages.

| ID | Finding against source | Confidence | Resolution |
|---|---|---|---|
| DQ-01 | `API_ENDPOINTS.md` claimed **19 routes**, but `lambda_handler` exposes **20 method/path combinations**; `GET /jobs/{jobId}/selfie` omitted. | Source verified | **Corrected**: full 20-route method/path table with per-route handler, inputs and outputs. |
| DQ-02 | Retry matrix and architecture still claimed Scene 2/3 built-in **10s/20s** waits. Active `google-video.yaml` uses **`Claim → Submit`**. | Source verified + prior guarded release 43824295664 | **Corrected**: no fixed inter-scene waits; separate 20s `WaitForVideo` GET-poll cadence retained. |
| DQ-03 | Retry/status/infra docs described assembly as **one-shot** with no Catch or safe retry. Active `assembly.yaml` contains Catch → 15s Wait → `RetryConfirmedAssemblyFailure`. | Source verified; prior resource release 43775514956 | **Corrected**: one safe FFmpeg-only retry, subject to persisted failed-worker condition. |
| DQ-04 | Top of status/API docs warned that any `assembly.FAILED` still produces permanent failure; deployed `assembly_failure_disposition` now avoids premature terminal state. | Source verified + prior guarded release 43835732389 | **Corrected**: first failure nonterminal `RETRYING`; final fail requires persisted second failed worker and failed named SFN. |
| DQ-05 | `JOB_STATUSES.md` said there was no global `VIDEO_2` field, but later `overall_job_progress` now computes `currentStage`. The nuance is **computed vs persisted**. | Source verified | **Corrected**: end-to-end projection exists, but no independently persisted `jobLifecycle`. |
| DQ-06 | `RETRIES_AND_TIMEOUTS.md` confused EventBridge handoff delay, Google GET status polling, manual new paid scene attempts and ffmpeg-only retry. | Source verified | **Corrected**: per-operation authority, deadline, retry triggers, terminal outcome, unknown-operation caution. |
| DQ-07 | Stage-specific historical docs refer to Replicate, disabled Google integration, no Stripe, and old UI/deploy workflows as though still current. | Historical source records vs current source | **Marked historical** with front-page warning and links to current contracts; original change evidence retained. |
| DQ-08 | `RESOURCE_CLEANUP.md` says Google paid is hard-disabled; runtime truth is governed by deployed CloudFormation `PaidVideoEnabled`, not the initial template default. | Source distinction; current AWS parameter not independently read | **Labelled historical**; current infrastructure and payment readiness explicit. |
| DQ-09 | The resilience audit primary RA-01 row still said `OPEN — fix first` after successful guarded code deployment. | GitHub prior action 43835732389 / source | **Corrected**: code and deployment verified, **live failure injection still open**. |
| DQ-10 | No entry point distinguished authoritative contracts from historical release plans and source/deployed/live verification. | Documentation inventory | **Corrected**: `docs/README.md` documents ownership, scope, evidence terms and change checklist. |
| DQ-11 | Old API response examples omitted the `terminal/outcome` progress fields now exposed, and described only legacy image status. | Source verified | **Corrected** with current synthetic examples and the separation of `job.status` (images) vs `job.progress` (end-to-end). |
| DQ-12 | No standing guard to detect future mismatches of HTTP method/path table, ASL waits and FFmpeg retry contract. | Repository workflow inspection | **Added** `tests/test_documentation_contract.py` and docs-only CI workflow; this is intentionally static and limited, not a substitute for full API tests. |

## API inventory methodology and boundaries

The dispatcher in `src/api/handler.py:lambda_handler` was inspected route-by-route. The canonical route table contains exactly **20 method/path combinations**, not including API Gateway CORS OPTIONS or internal IAM-authenticated Lambda events. The routes with most business risk are `POST /jobs` and image start, Google paid sequence / original scene / manual retry, status GETs, and assembly POST/GET. They use different idempotency mechanisms and should **not** be described as one universal retry policy.

The source establishes paths and code branches, not independently measured live HTTP response examples. Provider error metadata is optional; some statuses are read-only SFN-derived projections and may differ from persisted DDB per-scene fields. The developer `x-api-key` is not a payment entitlement.

## Error and retry review

- **Reference images:** only explicit 429 automatically reissues model generation; max eight attempts within 600s, while 3 images run concurrently. Timeout/unknown acceptance must not be reissued blindly.
- **Video generation:** single billable Submit per authorised attempt, no SFN Retry on Submit. `SUBMISSION_UNKNOWN` is deliberately persisted before the POST, so it cannot be interpreted as provider rejection. `PollVideo` read-only GET has up to three task retries, plus a normal 20s wait loop.
- **Handoffs:** event-driven and zero *intentional* fixed inter-scene wait, but EventBridge/task startup latency is real. Reconcile lost/duplicate events without chargeable POST reissue.
- **Assembly:** 15s delay before **one** retry of a confirmed failed FFmpeg worker; failed first attempt is not final. Timeout/unknown or inconsistent source/SFN state leads to review, not refund.
- **Terminal:** content block, definitive failed image job, exhausted confirmed video attempts, confirmed exhausted assembly retry; success requires expected final MP4 key and URL. Some final S3 availability/retention questions remain.
- **Browser:** GET status and presigned-media URL refresh do not resubmit video; expiry of a five-minute signed link is not object deletion.

## Verification evidence and unresolved items

**Updated docs do not mean the product is production-ready.** The following require separate work or proof:

1. **RA-01 live fault injection:** failed first FFmpeg attempt → successful retry; failed second attempt → correct durable final; unavailable Step Functions outcome → review. Deployed source was previously verified, not these live scenarios.
2. **RA-02…RA-12:** missed start events, two-hour watchdog deadline, Google uncertain provider acceptance, concurrency/terminal fencing races, secure customer recovery, payment/refund ledger, media retention and actual download delivery, observability.
3. **Live runtime drift:** independently compare active CloudFormation parameters and rendered state machines, IAM policies, Lambda artifact hashes, API Gateway stage and environment values to source; template defaults alone do not prove active settings.
4. **Actual API responses:** synthetic contract fixtures should become automated local/integration tests for status code, idempotency, response, backward compatibility, and per-route error conditions. This review did **not** execute all 20 live API endpoints.
5. **External provider contract:** Google's current quotas, billing and provider-side request-id behaviour were not independently reverified; those claims must be separately sourced before paid launch.
6. **Diagrams and historical screenshots:** the SVGs are retained as visual assets but were not individually rendered and validated against deployed AWS. Treat as illustrations, not definitive operational control documentation.

## Maintenance rules

When API/worker/ASL changes land, update the appropriate canonical contract in the same change, not by appending contradictory “updated below” warnings. All current docs should lead with **source review date, verification level, evidence paths, known limitations**. Preserve historical records but label them. Add regression assertions for new routes/retry states and verify against actual Lambda handler and ASL in code review. **Never run AWS deployment or paid Google requests merely to review documentation.**

**Review result:** Current primary docs corrected; historic pages explicitly classified; remaining live validation gaps retained. No application code or AWS resources modified.

## Documentation CI evidence — 16 November 2026

- **Passed:** [dedicated docs contract validation run 43838304123](https://github.com/rorygleeson/afl-video-platform/actions/runs/43838304123). All seven static source/document tests completed successfully. The workflow is read-only (GitHub checkout, Python, no AWS credentials and no Google requests).
- **Passed after final roadmap correction:** [run 43838407608](https://github.com/rorygleeson/afl-video-platform/actions/runs/43838407608), covering the current documentation inventory and assertions. This verifies checks in source; it is not an authenticated HTTP integration run or live AWS drift assessment.
- The dedicated `.github/workflows/validate-docs.yml` runs when docs, the API route handler, or relevant Google/FFmpeg ASL changes. Exact route response/error code behaviour still needs independent mocked/live integration test coverage.

**Resolution:** Primary canonical documentation reconciled against implementation; historical documents labelled; documentation regression gate added and passing. Functional resilience blockers RA-02…RA-12 remain tracked separately.


## Diagram audit and replacement — 16 November 2026

**Current vector diagrams are SVG, not Mermaid.** Their contents were reviewed against the current API, image/video workers, Step Functions and EventBridge code. The resulting drawings are:

| Diagram | Audit finding / fix |
|---|---|
| [architecture-high-level.svg](architecture-high-level.svg) | Replaced the obsolete Replicate image provider, per-scene manual video approval and missing assembly with direct Google Vertex image API, parallel image workflow, verified backend EventBridge handoffs, strictly sequential video and FFmpeg final assembly. |
| [generation-and-retries.svg](generation-and-retries.svg) | New source-aligned engineering flow: image 429 retry (up to eight total attempts/600s), no automatic paid Google video POST replay, read-only 20s poll loop, confirmed failure/manual scene retries (up to ten total attempts), and one safe FFmpeg-only retry after 15s. |
| [watchdog-architecture.svg](watchdog-architecture.svg) | Replaced with clearer one-job event-driven flow: image SFN RUNNING → EventBridge → named watchdog → 60s checks → safe verified event replay. Explicit 2h lifetime, lost-start-event limitation and separate existing five-minute image reconciler. |
| [mobile-ui-illustration.svg](mobile-ui-illustration.svg) | Preserved as historical artwork; prominently relabelled NOT CURRENT because it depicts an obsolete manual video-generation button. |

**Documentation usage:** `ARCHITECTURE.md` and `INFRASTRUCTURE.md` now reference the SVGs instead of Mermaid fenced diagrams. `docs/README.md` indexes all four assets; the old mobile illustration is historical. The diagrams are **source-aligned conceptual views**, not a live cloud resource snapshot or proof of event delivery.

**Validation:** [GitHub Actions documentation run 43839983352](https://github.com/rorygleeson/afl-video-platform/actions/runs/43839983352) passed. The new unit tests XML-parse every SVG, check that the index links exist and disallow Mermaid in the primary architecture/infrastructure documents. Full rendered cross-device layout review has not been independently performed; do not claim visual usability is proven from XML parsing alone.
