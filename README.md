# AFL Video Platform

Serverless AWS prototype for personalised AFL-themed reference images, with a separately planned video-generation and assembly stage.

**Current milestone (7 November 2026):** three parallel, personalised reference images generated from a customer's selfie, selected AFL team and three versioned scene masters. Jobs are independently tracked per scene and the phone page polls progress automatically. **Google Omni video generation, stitching and payments are not yet implemented.**

**Approved staged video design (8 November 2026):** [Video implementation plan](docs/VIDEO_IMPLEMENTATION_PLAN.md). This is documentation only; video generation has not been deployed. Each stage requires user approval before proceeding.\n\n## Architecture

**[Read the deployed AWS architecture, diagrams and end-to-end workflow](docs/ARCHITECTURE.md)**

The architecture document contains:

- A simple Mermaid AWS component diagram (website, API, processing, storage and Replicate).
- The step-by-step selfie upload and parallel image generation sequence.
- Essential API endpoints, job states, retries and failure handling.
- A short deployment diagram and a clear distinction between deployed services and the future Omni Flash video stage.

Video preparation: [versioned S3 prompt templates, name/team substitution and cost limits](docs/VIDEO_PROMPTS.md).

Additional technical notes: [Parallel scene processing](docs/PARALLEL_SCENES.md) and [the earlier single-scene proof of concept](docs/SCENE1_REPLICATE_PILOT.md).

## Development reference

- [Mobile test frontend](https://d9i3mlqvt0ks6.cloudfront.net)
- Application deployment workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
- CloudFormation templates: [`infrastructure/`](infrastructure/)
- API handler: [`src/api/handler.py`](src/api/handler.py)
- Image worker: [`src/image_worker/handler.py`](src/image_worker/handler.py)
- Scenario and team configuration: [`scenarios/afl-grand-final-winning-kick/v1/config.json`](scenarios/afl-grand-final-winning-kick/v1/config.json)

The current API key is for developer testing only; do not use it as consumer authentication.
