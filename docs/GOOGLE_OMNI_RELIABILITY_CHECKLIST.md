# Google Omni reliability checklist

Last reviewed: 2026-11-12. Keep items unchecked until implementation, tests, and deployment verification are complete. Never automatically retry a potentially billable video POST.

## Priority order

- [x] **1. Preserve detailed provider failure evidence (high).** At the first failed poll, persist safely bounded/redacted Google error message(s), error codes and relevant structured details; record HTTP status/request identifiers when available. Expose only safe metadata via operator diagnostics. Add no-paid-call tests; verify DynamoDB contents in a future failure.
- [x] **2. Make read-only polling resilient (high).** Back off/retry transient GET errors and timeouts, without ever retrying POST, changing the interaction ID, or creating a second paid scene. Recover workflow state from an already-created interaction. Add tests for network/429/5xx vs terminal provider errors.
- [ ] **3. Harden completed-MP4 retrieval (high).** Prevent oversized inline base64 payloads, response-size limits or Lambda memory/timeout failures from unnecessarily losing an already generated MP4. Preserve the interaction ID and allow safe GET recovery. Test bounded 720p payloads and oversized responses without new POSTs.
- [x] **4. Improve HTTP error visibility (medium).** Safely capture Google HTTP error code, request/correlation ID, relevant rate-limit/Retry-After metadata and bounded redacted structured provider error; never store credentials, raw images, prompts or media.
- [ ] **5. Record generation provenance (medium).** Model, scene duration, prompt/version digest, reference-image SHA-256, configuration and bounded polling/timing metrics; allow successful vs failed scene comparisons.
- [ ] **6. Apply provider-wide concurrency/rate controls (medium, pre-launch).** The existing 0/10/20-second per-batch stagger is *not* a global limiter across customers. Design admission controls that do not compromise idempotency or allow a second paid POST.

## Deployed verification (2026-11-12)

- **Items 1–2 completed and verified in AWS.** CloudFormation release run https://github.com/rorygleeson/afl-video-platform/actions/runs/43187758873 verified Google Lambda CodeSha256 and exact Step Functions states. `Submit` remains without Retry; only `PollVideo` includes bounded retry. These tests are offline and do not require new paid generation.
- Verification applies to the deployed code and infrastructure; actual retention of a future live Google failure message is not testable without a future provider failure.

## Current work (2026-11-12)

- **Items 1–2: PR #54 merged; guarded CloudFormation deployment verified.** https://github.com/rorygleeson/afl-video-platform/pull/54
- **Item 3: code merged but not activated or live-verified.** PR #55: https://github.com/rorygleeson/afl-video-platform/pull/55. Added opt-in GCS URI output and bounded authenticated download to the existing private S3 path; default remains inline. CI passed. Requires private GCS bucket, verified provider write + worker read permissions, reviewed CFN deployment and live verification before checking off. No paid test was run.
- **Nothing in this checklist authorizes a paid model run or an automatic paid generation retry.**

## Deployment run

- 2026-11-12: Guarded release completed and live AWS state verified: https://github.com/rorygleeson/afl-video-platform/actions/runs/43187758873.

## Item 3 activation prerequisites

- Confirm a private GCS bucket in Google project `afl-video-platform`; turn on uniform bucket-level access and public-access prevention.
- Verify the Agent Platform service agent can create output objects in the bucket and the federated `afl-video-worker@afl-video-platform.iam.gserviceaccount.com` identity can read the generated objects. Keep permissions bucket-scoped; confirm the precise writer identity from Google documentation/provider behaviour rather than assume access.
- Verify the chosen bucket/prefix, set the disabled-by-default CloudFormation `GoogleVideoGcsOutputPrefix` to that prefix (with trailing slash), and deploy worker + CFN via reviewed change set. The old default must remain inline for all pre-existing jobs.
- Confirm read-only GCS access with a non-sensitive known fixture object and test output-copy to private AWS S3 before any paid model request. Run a controlled paid scene only after explicit approval.
- Do not claim item 3 completed until the live GCS-to-S3 path is proven and the existing final MP4 assembly succeeds.

## Item 4 release (2026-11-12)

- Code merged via PR #56: https://github.com/rorygleeson/afl-video-platform/pull/56; tests passed.
- Guarded worker-code-only CloudFormation deployment successful and verified: https://github.com/rorygleeson/afl-video-platform/actions/runs/43190614335. Lambda CodeSha256 matches the approved package; Step Functions definition unchanged.
- This diagnostic change has no model POST retries, no prompt/UI changes, no new bucket and no GCS activation.

## Release discipline

- Implement in small, separately testable stages. Do not change prompts, UI layout, billing or reference-image generation as part of these reliability fixes.
- Confirm GitHub CI before merging. Confirm deployment separately; a merged PR is not proof of a live AWS deployment.
- Check off items only after code, tests and deployed behaviour have been verified. Record PR/commit or date beside each checked item.
