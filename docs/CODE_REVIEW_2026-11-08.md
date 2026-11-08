# Code review and bug backlog — 8 November 2026

Scope: repository source, API routes, frontend, Google/Replicate workers, CloudFormation, workflows and prompt configuration. **Static review, not live penetration/production testing.** The three individual paid MP4 scenes completed in a controlled browser pilot; their visual/audio quality is not yet verified.

## Findings — fix by business impact, not by adding test UI

| Priority | Finding | Why it matters | Recommended action |
|---|---|---|---|
| **P0 — before public launch** | API Gateway uses a single shared `x-api-key`, and job reads/actions have **no user ownership check**. | A leaked key can access guessed or disclosed job IDs and paid features; no order/customer isolation. | Authenticate end users, authorise each job/resource and enforce a verified payment entitlement at billable POST. Do **not** rely on hidden buttons or a random job ID. |
| **P0 — before public launch** | Paid Google calls lack a customer payment/quotas/budget control path. | Anyone holding the developer key can trigger provider costs through new jobs. | Implement server-side paid entitlements, per-user budgets, abuse control and alerts **before** sharing broadly. |
| **P1 — user-visible confirmed** | Native MP4 player can retain an expired S3 signed URL (5-minute validity). | Old clips may play from cache but fail when downloaded later; Scene 1 showed this. | Dedicated download buttons request fresh signed URLs. Verify end-to-end on phone; consider renewing player URL on a **new** playback attempt without interrupting playing media. |
| **P1 — recovery** | Google paid POST deliberately enters `SUBMISSION_UNKNOWN` *before* network submission. An actual failed/ambiguous submission is held for manual reconciliation. | Correctly avoids duplicate charges, but support cannot yet automatically resolve lost submissions; transient pre-POST UI warning looks like failure. | Preserve no-retry guarantee. Add **operator-only** reconciliation/runbook and distinguish transient submitting vs genuinely stuck in customer-facing status when building final product. |
| **P1 — retention** | Job item has seven-day DynamoDB TTL and source uploads/reference images expire; MP4 output is separately stored. | Old job/video may become undiscoverable even when MP4 bytes remain; no durable order/asset lifecycle. | Define retention/refund/privacy policy and durable completed-order metadata before launch. |
| **P1 — validation** | Google worker checks output is a bounded MP4, but **does not check that an audio stream or the requested commentary exists**. | A scene can be marked completed while its audio is missing/wrong. | Inspect audio/video stream metadata on real outputs; decide quality gate and manual fallback **before** automating paid retries. |
| **P2 — deployment** | Multiple stage-era GitHub workflows deploy overlapping API/frontend resources. | Sequential workflows still risk a later older revision overwriting a newer one; recently CI failed due to test expecting a removed UI label. | Consolidate to one maintained deployment path when stabilising; preserve smoke tests and block drift. |
| **P2 — legacy** | Separate mock worker/API route and old global mock admission slot coexist with real Google workflow. | More code and documentation to maintain; misleading claim of global real-video concurrency if read without context. | Mark mock path deprecated. Remove only once no tests or operational use depend on it. Real Google worker uses **per-job/per-scene** conditional claims, not a global concurrency slot. |
| **P2 — frontend** | Test UI is temporary and historically lost in-memory state after reload; a previous extra job-restore field was **unwanted and reverted**. | Growing test-only UI makes replacement harder. | Do not bolt on more test controls. Future consumer frontend should use a properly authenticated job/order list from the API. |

## What looks sound in the reviewed code

- API and worker require explicit per-scene Google approval; DynamoDB conditional write blocks a second attempt on the same job/scene.
- Status polling uses **GET** only; it never deliberately repeats a paid Google POST.
- Image-worker branches are isolated, using Step Functions Map concurrency 3. Replicate 429 retries use rejection-aware backoff, not blind retries after ambiguous submissions.
- Versioned prompts are rendered on the server using name/team/guernsey/commentary values.
- Private S3, temporary links and AWS-to-Google WIF keep provider credentials away from the browser.
- The paid worker persists MP4 in private S3 and records final state; playback is not pretending mock JSON is a real video.

## Not yet validated (do not call these tested)

- Authentic MP4 **audio-stream presence**, exact commentary, facial identity continuity and scene-to-scene transition.
- Native mobile download flow after the signed-URL fix.
- Concurrency under real multi-user load, throttling at provider quotas and recovery after partial infrastructure failures.
- Full consumer order and refund lifecycle (not built).
- Final MP4 assembly (not built).

## Recommended next phase

1. **Do not change the Google generation pipeline** while its three-scene pilot succeeds.
2. Run a focused test of existing MP4 downloads and audio, using existing endpoints; log actual bugs only.
3. Plan final assembly as one real product milestone.
4. Before a public beta, design authentication, payment entitlements, retention and budget enforcement together; don't add further experimental buttons to the temporary HTML client.

References: [current architecture](ARCHITECTURE.md), [complete API routes](API_ENDPOINTS.md), `src/api/handler.py`, `src/google_video_worker/handler.py`, `src/image_worker/handler.py`, `frontend/index.html`.
