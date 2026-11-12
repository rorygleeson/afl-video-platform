# Nano Banana template-lock prompt release — 12 November 2026

## Preconditions
- Confirmed live S3 image `config.json` exactly matched GitHub (SHA-256 `84b0c923015b7bca007399c799881a4b77c68aa01a10ddfb0a376414fb6ed962`).
- Proposed change merged via PR #57; all existing repository tests passed.
- Scoped to the shared image-editing prompt plus a Scene 1-only clarification. Scene 2 and 3 instructions are unchanged.

## Execution
- Guarded publish workflow checks the original S3 SHA and conditional ETag before a single config object write.
- Reads back the object, verifies SHA, and prints the new S3 VersionId.
- No Google model invocation, changes to master scene images, video animation, infrastructure or UI.
