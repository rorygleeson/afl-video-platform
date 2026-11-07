# AFL Video Platform

Serverless AWS application for personalised AFL-themed scene images, with a future video-generation stage.

**Current milestone (7 November 2026):** three parallel, personalised reference images generated from a customer's selfie, selected AFL team and three versioned scene masters. Jobs are independently tracked per scene and the phone page polls progress automatically. **Veo video generation, stitching and payments are not yet implemented.**

## Architecture

**[Read the deployed AWS architecture, diagrams and end-to-end workflow](docs/ARCHITECTURE.md)**

The architecture document contains:

- A simple Mermaid AWS component diagram (website, API, processing, storage and Replicate).
- The step-by-step selfie upload and parallel image generation sequence.
- Essential API endpoints, job states, retries and failure handling.
- A short deployment diagram and a clear distinction between deployed services and the future Veo stage.

Additional technical notes: [Parallel scene processing](docs/PARALLEL_SCENES.md) and [the earlier single-scene proof of concept](docs/SCENE1_REPLICATE_PILOT.md).

## Development reference

- [Mobile test frontend](https://d9i3mlqvt0ks6.cloudfront.net)
- Application deployment workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
- CloudFormation templates: [`infrastructure/`](infrastructure/)
- API handler: [`src/api/handler.py`](src/api/handler.py)
- Image worker: [`src/image_worker/handler.py`](src/image_worker/handler.py)
- Scenario and team configuration: [`scenarios/afl-grand-final-winning-kick/v1/config.json`](scenarios/afl-grand-final-winning-kick/v1/config.json)

The current API key is for developer testing only; do not use it as consumer authentication.
