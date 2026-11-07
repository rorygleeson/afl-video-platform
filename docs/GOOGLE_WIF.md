# AWS to Google Cloud authentication (no paid generation)

This stage adds a **keyless authentication probe only**. It does not submit any video requests to Gemini Omni / Veo, and it cannot incur video-generation charges.

## Current connection

- AWS account: `134387458447`, Region: `ap-southeast-2`
- AWS Lambda execution role: `afl-app-dev-video-worker-role`
- AWS Lambda: `afl-app-dev-video-worker`
- Google project: `afl-video-platform` (number `345956846110`)
- Workload Identity Pool: `afl-video-aws`
- AWS provider: `afl-aws-dev`
- Google service account: `afl-video-worker@afl-video-platform.iam.gserviceaccount.com`
- Required Google service account IAM binding: `roles/iam.workloadIdentityUser` assigned specifically to the AWS federated role.
- Required Google project IAM binding: `roles/aiplatform.user` assigned to the Google service account.

Our AWS worker uses `google-auth` with an in-memory `external_account` configuration, signs an AWS STS GetCallerIdentity request, exchanges this AWS identity using Google's Security Token Service and impersonates the Google service account. No service-account JSON private key or static Google API key is stored.

**Google APIs to enable:** `aiplatform.googleapis.com` (already enabled in the console), `iamcredentials.googleapis.com` (needed for service account impersonation), and `sts.googleapis.com` if the Google console requires explicit activation. API enablement can be verified on Google Cloud.

## Free authentication check

Visit the [AFL test website](https://d9i3mlqvt0ks6.cloudfront.net), enter the existing developer `x-api-key`, and tap **Test Google Cloud connection (no video charge)**. The browser calls:

```http
GET /dev/video/auth-check
x-api-key: <existing development API key>
```

Expected success: `{"status":"AUTHENTICATED","videoGenerationEnabled":false}`.

If it fails, the page shows an error. Inspect CloudWatch `/aws/lambda/afl-app-dev-video-worker` for the precise authentication or Google IAM error; no tokens or AWS credentials are written to logs.

**Guardrails:** Video Lambda only accepts `{"op":"auth_check"}`. There is no `generate` operation, no video API client, no model invocation permission specific to this job, and no frontend paid-video button. The API's auth-check is protected by the existing dev API Gateway API key but is not user-level authentication.

## Next stage (not deployed)

After `AUTHENTICATED` is verified, add one explicitly confirmed, cost-capped Scene 1 Google video submission using the supported Gemini Omni 1.2 Flash Interactions API, asynchronous polling, output storage to private S3, and separate scene status tracking. Validate cost and image identity before increasing concurrency.

## Planned video worker separation (design only)

Preserve the existing working authentication-probe Lambda. The planned **new video worker Lambda** will use a separate least-privilege AWS execution role; this new role must be explicitly permitted by Google WIF's role-restricted principal mapping and impersonation binding. Do not assume that authorising the probe role automatically authorises the new worker role. Verify permissions before deployment. Never create a static Google credential.

The primary storage target is existing private AWS S3 for inputs and output MP4s; prefer supported bounded inline image transfer and streamed/decoded video to S3. **The Vertex/Google Cloud Omni endpoint's exact image/video input-output and asynchronous response contract is a Stage 2 verification gate.** No new GCS bucket is planned unless required by that contract. See [implementation plan](VIDEO_IMPLEMENTATION_PLAN.md). All paid video generation stays off until a separately approved test.
