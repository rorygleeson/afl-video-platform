# Google Omni reliability checklist

Last reviewed: 2026-11-12. Keep items unchecked until implementation, tests, and deployment verification are complete. Never automatically retry a potentially billable video POST.

## Priority order

- [ ] **1. Preserve detailed provider failure evidence (high).** At the first failed poll, persist safely bounded/redacted Google error message(s), error codes and relevant structured details; record HTTP status/request identifiers when available. Expose only safe metadata via operator diagnostics. Add no-paid-call tests; verify DynamoDB contents in a future failure.
- [ ] **2. Make read-only polling resilient (high).** Back off/retry transient GET errors and timeouts, without ever retrying POST, changing the interaction ID, or creating a second paid scene. Recover workflow state from an already-created interaction. Add tests for network/429/5xx vs terminal provider errors.
- [ ] **3. Harden completed-MP4 retrieval (high).** Prevent oversized inline base64 payloads, response-size limits or Lambda memory/timeout failures from unnecessarily losing an already generated MP4. Preserve the interaction ID and allow safe GET recovery. Test bounded 720p payloads and oversized responses without new POSTs.
- [ ] **4. Improve HTTP error visibility (medium).** Safely capture Google HTTP error code, request/correlation ID, relevant rate-limit/Retry-After metadata and bounded redacted structured provider error; never store credentials, raw images, prompts or media.
- [ ] **5. Record generation provenance (medium).** Model, scene duration, prompt/version digest, reference-image SHA-256, configuration and bounded polling/timing metrics; allow successful vs failed scene comparisons.
- [ ] **6. Apply provider-wide concurrency/rate controls (medium, pre-launch).** The existing 0/10/20-second per-batch stagger is *not* a global limiter across customers. Design admission controls that do not compromise idempotency or allow a second paid POST.

## Current work (2026-11-12)

- **Items 1–2: code and offline tests in review.** PR #54: https://github.com/rorygleeson/afl-video-platform/pull/54. Not checked off until CI passes, changes are merged/deployed, and AWS is verified.
- **Item 3: design identified, implementation pending.** Google recommends `delivery: "uri"` with `gcs_uri` for large generated videos; the present `delivery: "inline"` makes every completed GET carry base64 MP4 data. Requires verified Google Cloud Storage permissions, secure download to existing private S3, and end-to-end tests. Do not switch the output contract without that work.
- **Nothing in this checklist authorizes a paid model run or an automatic paid generation retry.**

## Deployment run

- 2026-11-12: First guarded release aborted before AWS updates due to a live-template text mismatch. Corrected the parser; rerunning controlled release for items 1–2.

## Release discipline

- Implement in small, separately testable stages. Do not change prompts, UI layout, billing or reference-image generation as part of these reliability fixes.
- Confirm GitHub CI before merging. Confirm deployment separately; a merged PR is not proof of a live AWS deployment.
- Check off items only after code, tests and deployed behaviour have been verified. Record PR/commit or date beside each checked item.
