# AFL Video Prompts — personalisation and cost controls

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

The three source prompts live in GitHub at `scenarios/afl-grand-final-winning-kick/v1/video/` and are copied to the **private, versioned AWS S3 artifact bucket** by our deployment workflow. Google is a video-generation service, **not** our prompt database.

**Current state:** You can preview each resolved video prompt from the phone app. **Google paid video submission has not been implemented or enabled.** The existing Replicate *image* generation buttons and 3-image pipeline are unchanged.

## Customer fields and substitution

At the time of selfie upload/job creation the user must provide **two separate required fields**: `firstName` and `lastName`. The backend validates both (letters, spaces, hyphens, apostrophes, up to 40 characters each). The selected `team` is an **allowlisted AFL team ID**, never arbitrary prompt content.

| Template placeholder | Replaced by AWS using | Example |
|---|---|---|
| `$firstName` | Validated first name | Alex |
| `$lastName` | Validated surname | Tonelli |
| `$fullName` | First name + surname | Alex Tonelli |
| `$teamName` | Team display-name mapping | Collingwood Magpies |
| `$guernsey` | Existing `config.json` team guernsey style | Vertical black and white stripes |
| `$commentary` | Exact per-scene spoken commentary template, with the name/team resolved | Alex Tonelli kicks! It's through! ... |

We select a distinct prompt template and commentary line for each scene. The **starting image must be the already-personalised output of the Replicate image pipeline**, not the generic master. The prompts tell Google what action, camera motion and audio to generate, and explicitly instruct it to preserve the face and selected guernsey.

## Three separate no-charge scene previews

On the existing [AFL Video Lab](https://d9i3mlqvt0ks6.cloudfront.net), create a new test job with **first name, surname, team and selfie**, then tap:

- **Preview Scene 1 — Run-up**
- **Preview Scene 2 — Winning kick**
- **Preview Scene 3 — Celebration**

Each button reads the template and guernsey configuration from S3 via `GET /jobs/{jobId}/video/{sceneId}/preview` and displays the exact resolved prompt and commentary. This call **does not invoke Gemini, Veo or Replicate**. Existing image generation is a separate, explicitly paid button.

### S3 keys

```text
scenarios/afl-grand-final-winning-kick/v1/
  config.json                     # existing image/guernsey configuration
  video/
    config.json                   # version, concurrency=1, generation disabled
    scene1-video.txt              # run-up
    scene2-video.txt              # winning kick
    scene3-video.txt              # celebration
```

GitHub is the canonical source. When changed, GitHub Actions publishes the files to the existing versioned S3 bucket.

## Limits and next development stage

The video configuration currently sets `videoConcurrency: 1`, `maxPaidScenesPerRequest: 1`, `videoGenerationEnabled: false`. **These are documented future policy settings, not yet running video orchestration.** Concurrency one means at most one active provider request *at a time*; it does **not** prevent three queued requests from charging, which is why a separately enforced per-request one-scene limit is necessary.

Before enabling paid requests, implement and test:

1. An explicit **Generate Scene 1/2/3 Video** action, each submitting **only the selected scene** and requiring user confirmation.
2. A DynamoDB conditional claim and durable provider-request idempotency/recovery to avoid unintentional duplicate charges.
3. Step Functions async submit/poll/store MP4, independent scene states, signed playback, a real concurrency-one lock/queue and per-request cap.
4. An explicit model selection and verified Google API request schema/pricing, followed by one approved paid test.

No payment or video-charge operations are performed by the preview functionality.

## Approved next-stage use (design only; not deployed)

The three S3 templates will be reused verbatim or versioned after review for paid generation; each Generate Video button uses **only the corresponding personalised Replicate image** from the DynamoDB job's `scenes.sceneN.resultKey`, not the selfie or generic master. The paid submission resolves the same validated first/surname, team guernsey and per-scene commentary as today's free preview. Scene durations currently 8/8/10 seconds; validate model support before enabling paid calls. No automatic quality-based reruns.

Three new Generate Video buttons and the eventual Assemble Final Video action are **not deployed**. Cost controls are future enforcement requirements, not current active video limits. See [implementation plan](VIDEO_IMPLEMENTATION_PLAN.md). User approval required between stages.
