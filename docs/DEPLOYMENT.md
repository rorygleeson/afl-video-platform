# First application deployment

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

The one-time bootstrap stack `afl-video-bootstrap` already exists. **Application deployments do not recreate it.**

## 1. Merge this PR

The PR contains:
- `infrastructure/app/frontend.yaml` — private S3 + CloudFront + OAC
- `frontend/index.html` — standalone test interface, currently frontend-only
- `.github/workflows/deploy.yml` — GitHub Actions job triggered by pushes to `deploy`
- `scripts/git-deploy.sh` — portable Git-for-Windows shell script
- one IAM change to `infrastructure/bootstrap/bootstrap.yaml` — allow HTML upload to named frontend bucket

## 2. Update the existing bootstrap stack ONCE

Because the stack is already deployed, upload the updated `infrastructure/bootstrap/bootstrap.yaml` from `main` in the AWS CloudFormation **Update stack** screen in `ap-southeast-2`.

Use existing parameter values, review the change set and confirm `UPDATE_COMPLETE`. This adds scoped `s3:PutObject`/ `s3:GetObject` to the deployment role for the static frontend bucket only. Do not delete/recreate bootstrap. No GitHub repository variables or GitHub CLI are needed.

## 3. Configure the local alias once (Windows CMD)

From `C:\Dev\afl-video-platform`:

```cmd
git pull origin main
git config alias.deploy "!sh scripts/git-deploy.sh"
git deploy
```

The Git alias executes via Git for Windows' bundled shell; this is **not PowerShell**. The script rejects dirty working trees or branches other than `main`, pushes committed code to `main`, and pushes a unique commit marker with the exact same file tree to `deploy`. Only `deploy` triggers CD.

GitHub Actions runs validation and then deploys `afl-app-dev-frontend`, uploads `index.html`, and invalidates CloudFront. Watch the URL in the Actions log.

The test page intentionally has no live Generate action until the API/image pipeline is added. It does not persist API keys.

## Important security notes

This is a public HTTPS CloudFront page backed by a **private S3 bucket**. Do not put secrets into frontend HTML/JS. CloudFormation execution permissions remain broad; review and tighten them before using this deployment mechanism for production or an AWS account with other workloads.

## Approved staged video expansion — 8 November 2026 (NOT DEPLOYED)

This page originally covered initial frontend bootstrap; existing stack/bootstrap instructions above are historical. For the current system and next stages see [architecture](ARCHITECTURE.md) and [Video implementation plan](VIDEO_IMPLEMENTATION_PLAN.md).

**Do not create a feature branch.** Commit design/code changes to `main`, and deploy by the existing explicit `deploy` marker flow only at the relevant approved stage. A `main` commit alone does not deploy the app. New resources belong in the separate `afl-app-dev-video` CloudFormation stack; minimal additive backend API/IAM and frontend changes follow after infrastructure simulation, with no alterations to the existing Replicate worker/state-machine definition. Review CloudFormation change sets for replacements.

**Important existing deploy-packaging finding:** `.github/workflows/deploy.yml` currently copies `handler.py` and `video_prompts.py` into `dist/api`, but the ZIP command packages only `handler.py`. Before any API Lambda deployment, fix the ZIP step to include both modules and add a cold-import smoke test. Also verify retention: `results/` images and job TTL currently have a seven-day window; define separate `videos/` retention for later customer use.

Stage gates: 1 docs; 2 actual Google Omni API contract; 3 separate infrastructure & mock worker; 4 additive API and UI; 5 FFmpeg assembly; 6 explicitly authorised real paid tests. **Stop for user approval between stages.** Video generation remains disabled by default.
