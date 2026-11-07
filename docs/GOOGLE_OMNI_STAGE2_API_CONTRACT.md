# Stage 2 — Google Omni 1.2 Flash API contract research

**Reviewed 8 November 2026. Documentation-only research, no authenticated Google requests, paid calls, AWS deployment or resource changes.** This note records Google's published contract, not a live verification of our particular project's quota or IAM access.

## Confirmed from official Google documentation

- Model identifier: `gemini-omni-1.2-flash` (GA on Google Cloud 5 November 2026).
- Endpoint (Google Cloud / Vertex authenticated, global location): `POST https://aiplatform.googleapis.com/v1beta1/projects/afl-video-platform/locations/global/interactions` with Bearer token from existing Workload Identity Federation. REST response returns an interaction ID, not a completed video.
- Async: `"background": true`. Poll `GET https://aiplatform.googleapis.com/v1beta1/projects/afl-video-platform/locations/global/interactions/{INTERACTION_ID}` until `completed`, `failed` or other terminal status. Documented stored/background interaction retention up to 7 days. Do not retry an uncertain billable submit.
- Image-to-video: `input` can contain prompt text and an image; `generation_config.video_config.task = "image_to_video"`.
- General Interactions API schema defines `{"type":"image","data":"BASE64_ENCODED_IMAGE","mime_type":"image/png"}`, as well as URI inputs. Omni 1.2 Flash published image inline limit 20 MB; accepted PNG/JPEG/WebP etc. Start with image type from the existing S3 object's actual ContentType.
- Response format defines `{"type":"video","delivery":"inline","aspect_ratio":"9:16","resolution":"720p","duration":"8s"}`. Google's Vertex API docs explicitly support inline output bytes if no GCS output is specified. For `delivery:"uri"` on Vertex, `gcs_uri` is **required**; **no direct private S3 URI**. Therefore S3-only requires inline output and bounded processing in Lambda; do not assume a Google-hosted download URI on Vertex.
- Supported 1.2 Flash output duration image-to-video 3–15 seconds, so our current 8, 8 and 10 seconds are documented. Resolutions 360p, 720p, 1080p, 4K; output aspect 9:16 is supported.
- Published Google Cloud announcement 5 Nov 2026: **US$0.07/second at 720p** video. Our 8+8+10=26 seconds imply **US$1.82 output generation**, prior to input/thought token charges (if applicable), retries, exchange rate, tax, Replicate, AWS and fees. Google's older token-priced reference covers prior Omni models, not explicit 1.2 accounting; examine actual billing data before final consumer price.
- Existing project WIF auth probe passed in earlier test. New worker's **different AWS IAM role** will need its own matching Google workload identity service-account impersonation binding. Existing service-account `roles/aiplatform.user` is documented in repo, but we have **not** verified model/quota authorisation in this stage; auth success is not model access proof.
- Google states generation may take over one minute; Step Functions Standard async polling (small JSON) is appropriate. Google Cloud Vertex interactions GET result may embed `video/mp4` base64 in a JSON response. No API Gateway or Step Functions payload should contain video bytes.

## Candidate request shape — NOT executed or proof of successful model submission

```json
{
  "model": "gemini-omni-1.2-flash",
  "background": true,
  "input": [
    {"type": "text", "text": "PERSONALISED_S3_PROMPT_FOR_SCENE"},
    {"type": "image", "data": "BASE64_IMAGE_FROM_S3", "mime_type": "image/png"}
  ],
  "response_format": [
    {"type": "video", "delivery": "inline", "aspect_ratio": "9:16", "resolution": "720p", "duration": "8s"}
  ],
  "generation_config": {"video_config": {"task": "image_to_video"}}
}
```

Scene 3 substitutes `10s`. The request structure combines the official image-to-video example and the official general Interactions inline image/output schema. **Exact behaviour of background image-to-video with inline input and output must still be validated in the real Google project** once Stage 3 implementation is approved; do not label it end-to-end tested.

## S3-only size and recovery engineering

Existing private S3 bucket remains source and destination. For each scene, read the completed reference image with validated image ContentType and bounded size (<20 MB), encode inline, and use a Google HTTP client with explicit max response size/memory. A ~12 MB MP4 is ~16 MB base64; a 35 MB single MP4 would be ~47 MB base64. These are engineering estimates (4/3 expansion), not Google size guarantees. Initially allow 50 MB decoded output per clip with controlled temporary disk and memory and a fail-closed oversize check. Decode/download within the worker; write MP4 to `videos/{jobId}/sceneN.mp4`; verify object before status `COMPLETED`. Do not return base64 via Lambda response, Step Functions, API Gateway, DynamoDB or frontend APIs.

**Operational risk:** Google's docs do not give a guaranteed maximum size for an asynchronously retrieved inline video JSON response, nor prove that 50 MB outputs work for this project. Benchmark the *actual* first approved clip before committing to S3-only at larger scales; optionally fall back to a private GCS output bucket if real API payload constraints require it. Until then there is **no GCS bucket** in Stage 3 design.

Use provider status/interaction IDs plus conditional writes and a global generation lease across Google processing duration; never auto-resubmit ambiguous paid requests. Ensure lifecycle and 7-day source image/job expiration are accounted for. Concurrency one initially, architecture supports three later subject to provider quota.

## Stage 3 implementation blockers / checks

1. Model/API request shape is documented, **but no live model entitlement or quota check has been made** (no Google Cloud/AWS runtime access supplied here).
2. Verify the new AWS worker IAM role can exchange WIF and impersonate service account; the existing tested probe role does not automatically cover a new role.
3. Check model quota / rate limits and billing limits in actual Google project before first paid call; project-specific quotas are not inferable from public docs.
4. Build contract tests with mocked asynchronous POST/GET responses, input and output JSON plus oversized base64, failed interaction and uncertain submission.
5. Only with **separate user approval** deploy Stage 3 new stack with paid generation disabled. After further approval a single paid end-to-end clip can establish output envelope, bitrate, audio fidelity, latency and actual billing.

## Primary official sources

- Model GA/release notes: https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes
- Image-to-video / async endpoint and durations: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image
- Interactions API image `data` and output `inline` schema: https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/interactions-api
- Image media limits: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/image-understanding
- Pricing/launch announcement: https://cloud.google.com/blog/products/ai-machine-learning/gemini-omni-1-2-flash-generally-available/

**Stage 2 status: documentation contract researched. Real model access, quota, and inline output performance untested. Do not deploy or call model before the next user approval.**
