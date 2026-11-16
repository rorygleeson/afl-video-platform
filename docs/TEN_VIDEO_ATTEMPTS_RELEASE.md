# Ten manually confirmed paid attempts per video scene

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

2026-11-12. PR #60 raises the maximum from 3 to 10 total attempts
per scene. This is not an automatic retry loop. Retry remains allowed only
after a confirmed terminal Google provider failure and finished Step Functions
execution, with explicit confirmation and atomic DynamoDB reservation.

No other scene, reference image, video prompt, scene duration or stitch settings
will change. Historical provider interaction records remain archived.

Billing caution: Google Gemini billing FAQ says HTTP 400/500 errors are not
charged for tokens, but does not definitively establish whether async Omni
interactions which first return an ID and later fail incur a charge.

Deployment workflow patches existing CloudFormation Lambda code artifacts,
verifies exact live Lambda hashes and unchanged Step Functions definitions,
then publishes existing app.js and index.html and submits CloudFront
invalidation. Tests do not invoke the paid model. No paid generation
is triggered during deployment.
