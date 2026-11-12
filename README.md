# AFL Video Platform

**Turn a selfie into your own AI-generated AFL Grand Final highlight.**

A mobile-friendly web app that puts you into personalised football scenes. Choose your AFL team, upload a selfie and enter your name to create AI-generated action, celebration and commentary featuring you.

<img src="docs/screenshots/03-generated-video-sample.jpg" alt="Example personalised AFL video on a phone" width="420">

## How it works

1. **Choose your team and upload a selfie.** Enter your first and last name.
2. **Create reference images.** AI places you into three football moments, wearing your team's colours.
3. **Generate video scenes.** Animate the images into a run-up, a winning kick and a premiership celebration, with personalised commentary.
4. **Watch your highlights.** Preview the generated scenes and play your MP4 videos.

## Screenshots

| Selfie and team | AI-generated reference scenes | Video preview |
| --- | --- | --- |
| <img src="docs/screenshots/01-selfie-upload.png" alt="Select an AFL team and upload your selfie" width="240"> | <img src="docs/screenshots/02-reference-images.png" alt="Three personalised football reference images" width="300"> | <img src="docs/screenshots/03-generated-video-sample.jpg" alt="AI-generated AFL video playback" width="240"> |

## Architecture

The platform uses **AWS CloudFront and S3** for the web app and media, **API Gateway and Lambda** for its backend, **Step Functions** to coordinate image and video generation, and **DynamoDB** to track jobs. AI models generate personalised imagery and videos.

[**Architecture overview**](docs/ARCHITECTURE.md) · [**Architecture diagram**](docs/architecture-high-level.svg)

## Technical documentation

- [Architecture and system components](docs/ARCHITECTURE.md)
- [API endpoints](docs/API_ENDPOINTS.md)
- [Video prompts and personalisation](docs/VIDEO_PROMPTS.md)
- [Deployment and infrastructure](docs/DEPLOYMENT.md)
- [GitHub Actions release pipeline](.github/workflows/release.yml)

---
**AFL Video Platform** · Personalised AI-generated football highlights.
