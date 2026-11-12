# Isolated Google video scene retry release — 12 November 2026

## Scope and billing safety
- PR #58 merged: https://github.com/rorygleeson/afl-video-platform/pull/58
- Retry button for each independently failed Scene 1, 2 or 3; three personalised reference images and unaffected successful MP4s remain unchanged.
- Only an explicitly confirmed, fully completed Google provider failure may be retried. The old Step Functions execution must be SUCCEEDED (finished) and the original paid request ID must match.
- Each retry is a **new potentially billable Google model generation**, only after user confirmation. Maximum 3 paid attempts per scene.
- Atomic conditional DynamoDB retry reservation archives the prior provider attempt and prohibits double-click/concurrent duplicate claims.
- Existing Google POST retry safety remains unchanged; no automated paid resubmissions.

## Deployment safeguards
- Existing worker/API CloudFormation packages are patched with exact current source while retaining dependencies; change sets must only modify Lambda code and allowlisted dynamic references.
- Assert live Lambda and versioned CloudFormation artifacts are consistent before updating.
- Verify exact deployed Lambda CodeSha256, unchanged Step Functions definition and role after release.
- Publish only frontend app.js/index.html and invalidate existing CloudFront distribution.
- Deployment tests are offline; deployment does **not** trigger model generation.
