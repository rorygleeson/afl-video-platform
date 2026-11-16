# Stage 1B: selfie upload and jobs API

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

This phase is an **API and storage milestone**, not image generation. The image worker and three master frames will be integrated in the next phase.

## Deployment

After this PR is merged, the existing `git deploy` flow automatically:
1. deploys/updates `afl-app-dev-frontend`
2. builds `src/api/handler.py` as a Lambda ZIP and uploads it to the bootstrap artifact bucket
3. deploys/updates `afl-app-dev-backend` (API Gateway REST API, API key + usage plan, Lambda, private S3 selfie uploads, DynamoDB jobs)
4. reads `ApiBaseUrl` from the backend CloudFormation output
5. creates and uploads `frontend/config.js` to S3 and invalidates CloudFront, **so there is no API URL field on the test page**

No extra GitHub variables and no manual updates to `afl-video-bootstrap` are expected for this change. The app CloudFormation execution role provisions `afl-app-dev-api-role` with the existing permissions boundary.

## API key — enter once per browser tab

The API key is created automatically by CloudFormation but its secret **value is not committed to GitHub or rendered in CloudFormation outputs**.

AWS Console → API Gateway → API Keys → `afl-app-dev-test-api-key` → **Show** (value). Copy it into the mobile test page. It remains in memory only, not localStorage.

API Gateway API keys are **not user authentication**. This is a limited development test API with a usage plan (5 requests/second, burst 10, 10,000 per month), not a public production service. Do not publish its key.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/scenarios` | Available scenarios; image generation flag false |
| POST | `/uploads` | Returns a 5-minute S3 **presigned POST** form enforcing JPEG/PNG/WebP and up to 10MB |
| POST | `/jobs` | Verifies S3 upload exists, creates an idempotent DynamoDB job |
| GET | `/jobs/{jobId}` | Retrieves job status |
| GET | `/jobs/{jobId}/images` | Returns empty images array until image worker exists |

All endpoints except preflight OPTIONS require `x-api-key`.

`POST /uploads` body: `{"contentType":"image/jpeg","sizeBytes":12345}`.

Upload using FormData, adding all returned `upload.fields` and then the file, POST to `upload.url`.

`POST /jobs` body: `{"uploadKey":"uploads/...jpg","scenarioId":"afl-grand-final-winning-kick","team":"carlton"}`, with header `Idempotency-Key: <random UUID>`.

Job status is deliberately **AWAITING_IMAGE_PIPELINE**, not `COMPLETED`. The frontend displays successful staging but does not claim generated reference images exist.

Uploads expire after 7 days via S3 lifecycle; DynamoDB jobs have a 7-day TTL (DynamoDB removal is asynchronous). Both buckets/tables are private; failed/deleted CloudFormation stacks retain data resources for safe recovery.

## Next implementation phase

Add the versioned scenario configuration and S3 master frames, Replicate credentials in Secrets Manager, the Step Functions Standard Map workflow, its worker Lambda and reconciler. Connect it to the existing `POST /jobs` and change the status transitions to `GENERATING_IMAGES` and `COMPLETED`.

No video generation, payments or account system in Stage 1.
