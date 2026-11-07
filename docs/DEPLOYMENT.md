# First application deployment

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
