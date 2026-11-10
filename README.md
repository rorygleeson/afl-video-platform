# AFL Video Platform

**Turn your selfie into an AFL Grand Final winning-goal video.**

A mobile-friendly AI video application where you choose an AFL team, upload a selfie and enter your name. It creates a personalised football highlight with cinematic action and commentary featuring your name and team.

## How it works

1. **Upload a selfie** and select your AFL team.
2. **Generate three reference images** — the breakaway, winning kick and premiership celebration.
3. **Create three video scenes** with Google Gemini Omni 1.2 Flash, using the personalised images and commentary prompts.
4. **Combine and download** the scenes as one final MP4.

The developer pilot uses **Replicate** for reference-image generation and **Google Vertex AI** for video generation. **AWS Lambda, Step Functions, DynamoDB and S3** coordinate processing and store the results; the web app runs on **CloudFront**.

## Mobile app screenshots

Three screenshots of the working pilot show selfie/team selection, the three generated reference images and a completed **17-second final video** with a download button.

*Screenshots to be added to `docs/screenshots/`.*

## Project status

**Developer pilot:** reference images, video generation, assembly and final playback have been demonstrated. Some video requests fail Google safety or quota checks; reliability and launch readiness remain work in progress. Consumer login and payments are not yet implemented.

**Current access:** development `x-api-key` — **no Cognito**.

## Technical documentation

- [Architecture and AWS diagram](docs/ARCHITECTURE.md)
- [API endpoints](docs/API_ENDPOINTS.md)
- [Scenario prompts](docs/VIDEO_PROMPTS.md)
- [Release workflow](.github/workflows/release.yml)
