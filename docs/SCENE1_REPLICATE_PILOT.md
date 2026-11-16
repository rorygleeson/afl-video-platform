# Stage 1C — paid Scene 1 pilot

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

The Stage 1B selfie upload/API-key flow remains unchanged. **Uploading does not trigger any Replicate spending.**

This implementation adds an explicit **Generate Scene 1 with Replicate (paid API)** action. Confirm the browser dialog to start exactly one async Replicate `google/nano-banana-pro` edit. Scene 2 and Scene 3 are defined in versioned config but **disabled** until the pilot quality is approved.

## Sources and configuration

S3 private/versioned assets (existing, in Sydney):

- `s3://afl-video-bootstrap-artifactbucket-xiuriidgyft4/scenarios/afl-grand-final-winning-kick/v1/scene1-start.png`
- `.../scene2-start.png`
- `.../scene3-start.png`

GitHub `scenarios/afl-grand-final-winning-kick/v1/config.json` holds the scenario IDs, operation/mode, selected-team kit descriptions and master keys. Every deployment verifies all three PNGs are present then publishes that JSON to the same S3 prefix.

**AWS secret:** `afl-app/dev/replicate-api-token` as JSON containing `REPLICATE_API_TOKEN`. The Lambda role can read this secret at runtime; no Replicate token is in source, GitHub or public HTML.

## Orchestration

- `POST /jobs/{jobId}/generate`: requires `x-api-key`, explicitly starts one deterministic Step Functions Standard execution (name = jobId).
- `Submit` worker: loads master/config, presigns *read-only* master and selfie S3 URLs for 30 minutes, builds the model prompt from the selected team's guernsey description, calls Replicate's **asynchronous official model endpoint**, saves the prediction ID.
- Step Functions Wait → Poll → Choice loops with bounded timeout; only read-only poll is retried.
- Successful prediction: download the single image from Replicate's delivery domain to private S3 `results/{jobId}/scene1.png`, mark `SCENE1_COMPLETED`.
- Failed prediction or task: mark `FAILED` without resubmitting a potentially billable call.
- `GET /jobs/{id}/images`: returns a 5-minute presigned GET URL for Scene 1. Browser's **Refresh job status** retrieves and displays it.
- Repeated `/generate` requests don't create a second prediction for that job under Step Functions' execution-name semantics.

The prompt asks for the uploaded selfie as the **only facial identity** and the selected team's guernsey description. A generative editor may still fail to preserve identity accurately; compare results before proceeding to scenes 2 and 3. Guernseys here are team-colour/design inspired and omit licensed badges and sponsors.

## Deployment

Merge the PR after CI passes, then from `C:\Dev\afl-video-platform`:

```cmd
git pull origin main
git deploy
```

No new bootstrap IAM permissions or manual CloudFormation update expected. The application stack creates the worker Lambda, bounded execution role, Step Functions Standard state machine and its IAM role.

### Safe first test

1. Refresh the CloudFront frontend.
2. Enter `x-api-key`, select a team and selfie, upload and create a job.
3. Confirm the job exists without Replicate charges.
4. Tap **Generate Scene 1 with Replicate (paid API)** and explicitly approve one paid image request.
5. Tap **Refresh job status** to observe `GENERATING_IMAGES`, `SCENE1_COMPLETED` or `FAILED`.
6. Review the generated image on the phone for facial fidelity and guernsey accuracy.

**Limits:** This is a deliberate single-scene proof of concept, not the final three-scene parallel workflow. It currently has no webhook reconciliation for the rare case of a Replicate request that succeeds remotely but returns an unknown result to Lambda. A new paid request must never be automatically retried under that uncertainty. AWS API keys are not user authentication, and the dev endpoint should not be publicly shared with its key.

