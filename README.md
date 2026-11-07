# AFL Video Platform

Serverless AWS application for personalised AFL-themed scene images, with a future video-generation stage.

**Current milestone (7 November 2026):** three parallel, personalised reference images generated from a customer's selfie, selected AFL team and three versioned scene masters. Jobs are independently tracked per scene and the phone page polls progress automatically. **Veo video generation, stitching and payments are not yet implemented.**

## Architecture

**[Read the deployed AWS architecture, diagrams and end-to-end workflow](docs/ARCHITECTURE.md)**

The architecture document contains:

- A Mermaid infrastructure/component diagram (CloudFront, S3, API Gateway, Lambda, DynamoDB, Step Functions, Replicate, Secrets Manager, EventBridge).
- The end-to-end selfie upload / job creation / parallel image generation sequence diagram.
- API endpoints, DynamoDB state model, retry/idempotency and recovery rules.
- GitHub Actions OIDC + CloudFormation deployment diagram and an as-built vs future-stage capability matrix.

Additional technical notes: [Parallel scene processing](docs/PARALLEL_SCENES.md) and [the earlier single-scene proof of concept](docs/SCENE1_REPLICATE_PILOT.md).

## Development reference

- [Mobile test frontend](https://d9i3mlqvt0ks6.cloudfront.net)
- Application deployment workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
- CloudFormation templates: [`infrastructure/`](infrastructure/)
- API handler: [`src/api/handler.py`](src/api/handler.py)
- Image worker: [`src/image_worker/handler.py`](src/image_worker/handler.py)
- Scenario and team configuration: [`scenarios/afl-grand-final-winning-kick/v1/config.json`](scenarios/afl-grand-final-winning-kick/v1/config.json)

The current API key is for developer testing only; do not use it as consumer authentication.
