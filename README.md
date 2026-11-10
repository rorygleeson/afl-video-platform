# AFL Video Platform

<img src="docs/screenshots/00-hero-video-sample.jpg" alt="Example AI-generated AFL premiership video playing on a phone" width="420">

**Turn your selfie into an AFL Grand Final winning-goal video.**

A mobile-friendly AI video application where you choose an AFL team, upload a selfie and enter your name. It creates a personalised football highlight with cinematic action and commentary featuring your name and team.

## How it works

1. **Upload a selfie** and select your AFL team.
2. **Generate three reference images** — the breakaway, winning kick and premiership celebration.
3. **Create three video scenes** with Google Gemini Omni 1.2 Flash, using the personalised images and commentary prompts.
4. **Combine and download** the scenes as one final MP4.

The developer pilot uses **Replicate** for reference-image generation and **Google Vertex AI** for video generation. **AWS Lambda, Step Functions, DynamoDB and S3** coordinate processing and store the results; the web app runs on **CloudFront**.

## Mobile app screenshots

Actual screenshots from the developer pilot, in order:

**1. Select your team and upload a selfie**

<img src="docs/screenshots/01-selfie-upload.jpg" alt="Mobile AFL app showing team, name and selfie upload" width="320">

**2. Generate three personalised reference images**

<img src="docs/screenshots/02-reference-images.jpg" alt="Three completed personalised AFL scene reference images" width="520">

**3. See an example of the generated video**

<img src="docs/screenshots/03-generated-video-sample.jpg" alt="Gallery view of an AFL video scene with a personalised player" width="320">

*The third screenshot is a frame shown in the phone gallery, not the final-video download screen.*

## Project status

**Developer pilot:** reference images, video generation, assembly and final playback have been demonstrated. Some video requests fail Google safety or quota checks; reliability and launch readiness remain work in progress. Consumer login and payments are not yet implemented.

**Current access:** development `x-api-key` — **no Cognito**.

## Technical documentation

- [Architecture and AWS diagram](docs/ARCHITECTURE.md)
- [API endpoints](docs/API_ENDPOINTS.md)
- [Scenario prompts](docs/VIDEO_PROMPTS.md)
- [Release workflow](.github/workflows/release.yml)
