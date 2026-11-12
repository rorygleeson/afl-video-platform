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

The platform runs on **AWS** and integrates with **Google Cloud** for AI video generation. **Amazon CloudFront and S3** host the web app and store images and videos; **API Gateway and Lambda** provide the backend; **AWS Step Functions** coordinates processing; and **DynamoDB** tracks each job. Personalised reference images are produced using **Replicate's Google Nano Banana Pro model**, while **Google Vertex AI's Interactions API** uses **Gemini Omni 1.2 Flash** to generate the football video scenes. **Google Cloud Workload Identity Federation** enables the AWS video worker to authenticate securely to Google Cloud.

[**Architecture overview**](docs/ARCHITECTURE.md) · [**Architecture diagram**](docs/architecture-high-level.svg)

## Technical documentation

- [Architecture and system components](docs/ARCHITECTURE.md)
- [API endpoints](docs/API_ENDPOINTS.md)
- [Video prompts and personalisation](docs/VIDEO_PROMPTS.md)
- [Deployment and infrastructure](docs/DEPLOYMENT.md)
- [GitHub Actions release pipeline](.github/workflows/release.yml)

---
**AFL Video Platform** · Personalised AI-generated football highlights.
