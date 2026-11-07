# AFL Video Platform — Architecture

**Status:** Working image-generation prototype (November 2026)  
**Environment:** AWS Sydney (`ap-southeast-2`), `dev`  
**Website:** https://d9i3mlqvt0ks6.cloudfront.net

> **What works today:** A user chooses an AFL team, uploads a selfie, and generates three personalised still images. Each image is tracked separately and displayed on the phone as soon as it finishes. **Video generation is the next phase, not yet built.**

## 1. Architecture at a glance

```mermaid
flowchart TB
    Phone["Mobile website"]
    Web["CloudFront and private S3"]
    API["API Gateway and API Lambda"]
    DB[("DynamoDB jobs")]
    Media[("Private S3 selfies and results")]
    SF["Step Functions - three parallel scenes"]
    Worker["Image worker Lambda"]
    Masters[("S3 master images and configuration")]
    Secret["Secrets Manager"]
    AI["Replicate - Nano Banana Pro"]

    Phone --> Web
    Phone --> API
    Phone -->|"Upload selfie directly"| Media
    API <--> DB
    API -->|"Start generation"| SF
    SF -->|"Run scene 1, 2 and 3"| Worker
    Worker <--> DB
    Worker <--> Media
    Worker --> Masters
    Worker --> Secret
    Worker <--> AI
    API -->|"Return signed image links"| Phone
```

The website is served by **CloudFront** from private **S3**. The **API Lambda** creates jobs, verifies uploads and starts generation. **Step Functions** coordinates three branches. The **image worker Lambda** calls Replicate and writes the results to private S3. **DynamoDB** stores each job's progress.

## 2. What happens when someone generates images?

```mermaid
sequenceDiagram
    actor User
    participant API as API Lambda
    participant S3 as Private S3
    participant DB as DynamoDB
    participant SF as Step Functions
    participant Worker as Image worker
    participant AI as Replicate

    User->>API: Request a selfie upload
    API-->>User: Return signed S3 upload form
    User->>S3: Upload selfie
    User->>API: Create job with team selection
    API->>DB: Save new job
    API-->>User: Return job ID
    User->>API: Confirm Generate 3 Images
    API->>SF: Start job execution
    SF->>Worker: Start three parallel scene branches
    Worker->>AI: Submit image edit for each scene
    AI-->>Worker: Return prediction IDs
    loop Until each scene finishes
        SF->>Worker: Check prediction status
        Worker->>AI: Fetch prediction status
        AI-->>Worker: Return prediction status
    end
    Worker->>S3: Save each completed image
    Worker->>DB: Update individual scene statuses
    SF->>Worker: Finalise overall job
    Worker->>DB: Save overall job status
    loop Every 5 seconds while active
        User->>API: Read job status and completed images
        API-->>User: Return progress and signed image links
    end
```

The diagram focuses on the main steps. The three branches run concurrently using a Step Functions **Map state with maximum concurrency 3**. Each scene has its own submit, wait, poll and save actions.

### The three scenes

| Scene | Personalisation |
|---|---|
| **1. Build-up** | Selfie face and selected-team guernsey on the player running with the ball |
| **2. Approach to kick** | Selfie face and selected-team guernsey on the player preparing to kick |
| **3. Celebration** | Selfie face on the main player and matching team guernseys on teammates |

The three source images are stored under `scenarios/afl-grand-final-winning-kick/v1/` in the private, versioned artifact bucket. The team's guernsey descriptions and editing instructions are in [scenario configuration](../scenarios/afl-grand-final-winning-kick/v1/config.json).

Scene 2 is intentionally a **starting position**. The actual kick will be animated later.

## 3. How jobs are tracked

Each job is a DynamoDB item with an overall status and three independently tracked scenes.

| Overall job status | Meaning |
|---|---|
| `AWAITING_IMAGE_PIPELINE` | Selfie uploaded, waiting for Generate |
| `GENERATING_IMAGES` | One or more scene branches are running |
| `COMPLETED` | All three images succeeded |
| `PARTIAL_SUCCESS` | Some images succeeded, some failed |
| `FAILED` | No images succeeded or setup failed |

Each scene moves through `PENDING`, `SUBMITTING`, `GENERATING` and `COMPLETED` or `FAILED`. We save its Replicate prediction ID, S3 result key and a safe failure code where applicable.

The phone polls the existing API **every five seconds**, shows each finished thumbnail without reloading it, pauses polling when the tab is hidden and stops at a terminal status. Polling does **not** create extra image-generation requests.

### Failure handling

- Generation is **explicitly confirmed** before up to three paid Replicate predictions begin. Uploading a selfie does not trigger Replicate.
- Conditional DynamoDB updates and a named Step Functions execution help prevent duplicate requests for the same job.
- Confirmed Replicate **HTTP 429** rejections can be retried up to **six total attempts**, respecting `Retry-After` or using exponential backoff and jitter (adapted from SkyGrid).
- An uncertain submission, such as a network timeout, is **not automatically resubmitted** because it may already have been accepted and charged.
- One scene can fail without losing the completed images from the other scenes.
- An **EventBridge** rule checks every five minutes for jobs stuck beyond 45 minutes and marks them terminal without launching new predictions.

## 4. AWS components

| Service | Purpose |
|---|---|
| **CloudFront + private S3** | Host the mobile test website |
| **API Gateway REST API** | Route `/dev` requests with a development API key |
| **API Lambda** | Selfie upload links, jobs, status and signed result links |
| **S3 uploads/results bucket** | Store customer selfies and generated PNGs |
| **DynamoDB** | Job status and per-scene state |
| **Step Functions Standard** | Run and coordinate three parallel scene jobs |
| **Image worker Lambda** | Replicate submit, poll, save, failure handling and recovery |
| **S3 artifact bucket** | Versioned master images, scenario config and deployment ZIPs |
| **Secrets Manager** | Replicate API token |
| **EventBridge** | Recover abandoned jobs |
| **CloudWatch Logs** | API and worker troubleshooting |
| **GitHub Actions + CloudFormation** | Validate and deploy the platform |

**Region:** `ap-southeast-2` (Sydney). The deployed Step Functions resource retains its historical name `afl-app-dev-scene1-pilot`, despite now running three scenes.

**Developer API:**
`https://ue36acwvrc.execute-api.ap-southeast-2.amazonaws.com/dev`

| Method | Endpoint |
|---|---|
| GET | `/scenarios` |
| POST | `/uploads` |
| POST | `/jobs` |
| POST | `/jobs/{jobId}/generate` |
| GET | `/jobs/{jobId}` |
| GET | `/jobs/{jobId}/images` |

Uploaded selfies and generated outputs currently expire from S3 after **seven days**. Completed image links are short-lived, signed URLs.

## 5. How we deploy

```mermaid
flowchart LR
    Git["GitHub main"]
    Checks["Automated tests"]
    Deploy["Deploy branch"]
    Actions["GitHub Actions"]
    CF["AWS CloudFormation"]
    AWS["AWS application"]

    Git --> Checks
    Checks --> Deploy
    Deploy --> Actions
    Actions -->|"AWS OIDC role"| CF
    CF --> AWS
```

The one-time bootstrap creates deployment roles, a permissions boundary and the private artifact bucket. GitHub Actions uses **OIDC** to assume the AWS deployment role. **CloudFormation**, not Terraform, manages application infrastructure.

Core source files: [frontend](../frontend/index.html), [API Lambda](../src/api/handler.py), [image worker](../src/image_worker/handler.py), [frontend stack](../infrastructure/app/frontend.yaml), [backend stack](../infrastructure/app/backend.yaml), [deploy workflow](../.github/workflows/deploy.yml).

## 6. Approved next architecture — not yet deployed (8 November 2026)

The current image workflow remains unchanged. We will add a separate `afl-app-dev-video` CloudFormation stack containing a **new video Lambda and new Step Functions Standard state machine**. The existing API Gateway/API Lambda will later expose individual paid Generate Video actions, separate from free prompt previews; each action reads the corresponding completed image S3 `resultKey`, adds a fully rendered scene-specific S3 prompt, and invokes **Gemini Omni 1.2 Flash** only after contract verification and explicit approval. This is a **future implementation**, not a deployed video pipeline.

Three independently generated MP4s go to the **existing private uploads/results S3 bucket** under `videos/{jobId}/sceneN.mp4`. A separate FFmpeg assembly Lambda and an explicit **Assemble Final Video** button will produce `videos/{jobId}/final.mp4` only when all three clips are ready. The approximate measured example is 35 MB total; the exact Google output mechanism and size bounds remain to be verified. **No new Google Cloud Storage or AWS bucket is assumed** unless the actual Google API contract demands one. Never expose the private AWS bucket to Google; transfer image bytes and video output via the worker using supported API formats.

Store independent `videos` and `assembly` DynamoDB states, preserving the original `scenes` map. Enforce one active async paid provider operation globally for development, durable submission idempotency and a hard no-resubmit rule for unknown paid submission outcomes. Retain existing free Google auth check, three-image Replicate pipeline and free previews. Future parallel three-video execution is an explicit later enablement, not something automatically switched on.

Full design, endpoint contracts, IAM/storage details, safe change sequence and **required user approval after each stage:** [Video implementation plan](VIDEO_IMPLEMENTATION_PLAN.md).

## 7. Next phase — not implemented yet

**Google Omni Flash video generation** will animate the three personalised still-image starting frames. We will design the scene prompts, clip generation, commentary/audio, video stitching and final playback separately. There is currently **no Veo integration, video stitching, payment system or customer account login**.

**Before a consumer launch**, we also need proper authentication, payments, monitoring/alerts, cost controls, data/privacy policies and more robust reconciliation of uncertain provider submissions.

---

**Scope:** This page describes the working prototype. For detailed implementation notes, see [parallel-scene processing](PARALLEL_SCENES.md). Update this document when the architecture changes.
