# Isolated scene-retry UI publish after guarded backend release

The guarded release https://github.com/rorygleeson/afl-video-platform/actions/runs/43208416471
verified both updated AWS Lambdas, then stopped before publishing UI because
the frontend CloudFormation stack had not been updated since its creation.
This follow-up publishes **only** frontend index.html and app.js, after
comparing actual AWS Lambda artifact bytes to the current GitHub source.

No Lambda, Step Functions, CloudFormation, Google model, job state or
reference images are changed. The preflight accepts only healthy CREATE_COMPLETE
or UPDATE_COMPLETE frontend CloudFormation states. The release invalidates the
two existing CloudFront files and waits for invalidation completion.

Release guard corrected to recognise dynamically constructed JavaScript control IDs; no backend change.

The frontend files and CloudFront invalidation submission succeeded in the previous run; only GetInvalidation was denied by the existing least-privilege deployment role. The corrected release verifies S3 contents and reports invalidation submission without claiming completion.

## Frontend-only diagnostics cleanup

PR #59 moved failure diagnostics and transient retry logs to bottom Developer Diagnostics and clears stale retry messages on completed MP4s. No backend updates. This UI-only release is gated on exact deployed backend code hashes and frontend regression tests.
