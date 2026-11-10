# AFL Video Platform

AWS serverless pilot for personalised AFL Grand Final videos.

**Status (8 November 2026):** selfie upload, **three personalised reference images** (Replicate/Nano Banana Pro), three independently generated **real Google MP4s** (8 / 8 / 10 seconds), free prompt previews, browser playback and signed downloads have been implemented. All three paid MP4 scenes succeeded in a controlled test. Native audio quality and mobile downloads still need validation.

**Not built:** final three-clip assembly, consumer login, payments, per-customer entitlements and production-scale cost controls.

## Start here

- [High-level architecture diagram](docs/architecture-high-level.svg) — user → frontend → API → Step Functions / Lambda → providers and storage
- [Architecture and simple end-to-end flow](docs/ARCHITECTURE.md) — current system overview
- [Complete API endpoint inventory](docs/API_ENDPOINTS.md) — browser contract for a future replacement frontend
- [Prioritised bug/risk review](docs/CODE_REVIEW_2026-11-08.md) — what to fix and what not to overbuild
- [IaC operating model and pause checklist](docs/PRODUCTION_PIPELINE_PLAN.md#iac-operating-model-and-pause-checkpoint--10-november-2026) — CloudFormation ownership, verified release checks and remaining infrastructure work

## Code map

- `frontend/index.html` — **temporary** mobile web test client
- `src/api/handler.py` — API Gateway Lambda, jobs, paid approvals, signed media links
- `src/image_worker/handler.py` — parallel Replicate image creation
- `src/google_video_worker/handler.py` — paid Google interactions/polling/MP4 save
- `src/api/video_prompts.py` — server-rendered personalised prompts
- `scenarios/afl-grand-final-winning-kick/v1/` — versioned masters and config
- `infrastructure/` — CloudFormation stacks
- `.github/workflows/` — GitHub Actions CI/deploy; legacy stage workflows remain

The frontend uses REST APIs for all job actions and state, plus **presigned S3 upload/download URLs** for large files. This separation is intentional: future Android/iOS/web frontends can reuse the backend without copying the current test page.

**Security:** `x-api-key` is a development key, **not** consumer authentication. Do not publish the paid endpoint before adding user/job ownership and payment entitlement enforcement.

Older stage-specific docs remain as implementation history; use the three current docs above for the present state.
