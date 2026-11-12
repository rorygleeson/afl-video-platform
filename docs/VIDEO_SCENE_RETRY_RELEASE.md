# Isolated Google video scene retry release — 12 November 2026

## Scope and billing safety
- PR #58 merged: https://github.com/rorygleeson/afl-video-platform/pull/58
- Retry button for each independently failed Scene 1, 2 or 3; three personalised reference images and unaffected successful MP4s remain unchanged.
- Only an explicitly confirmed, fully completed Google provider failure may be retried. The old Step Functions execution must be SUCCEEDED (finished) and the original paid request ID must match.
- Each retry is a **new potentially billable Google model generation**, only after user confirmation. Maximum 10 paid attempts per scene.
- Atomic conditional DynamoDB retry reservation archives the prior provider attempt and prohibits double-click/concurrent duplicate claims.
- Existing Google POST retry safety remains unchanged; no automated paid resubmissions.

## Deployment safeguards
- Existing worker/API CloudFormation packages are patched with exact current source while retaining dependencies; change sets must only modify Lambda code and allowlisted dynamic references.
- Assert live Lambda and versioned CloudFormation artifacts are consistent before updating.
- Verify exact deployed Lambda CodeSha256, unchanged Step Functions definition and role after release.
- Publish only frontend app.js/index.html and invalidate existing CloudFront distribution.
- Deployment tests are offline; deployment does **not** trigger model generation.

## 13 November 2026 follow-up
- Retry eligibility now recognises all persisted terminal Google statuses (failed, cancelled, incomplete, budget_exceeded), with the existing workflow-finished gate before new paid submission.
- Failed/review-required scenes without an available retry explain the blocker in UI and Developer Diagnostics. Uncertain Google POST and AWS workflow failure stay non-retryable.
- Regression cases cover two independently failed scenes and workflow-review state.
