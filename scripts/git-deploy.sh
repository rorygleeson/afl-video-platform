#!/bin/sh
# One-command deployment trigger. Uses Git for Windows' bundled sh; no gh CLI.
set -eu
current_branch=$(git branch --show-current)
if [ "$current_branch" != "main" ]; then
  echo "git deploy must be run from main (current: $current_branch)" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean. Commit or stash changes before deploying." >&2
  exit 1
fi
git push origin main
source_sha=$(git rev-parse HEAD)
tree_sha=$(git rev-parse HEAD^{tree})
if git ls-remote --exit-code --heads origin deploy >/dev/null 2>&1; then
  git fetch origin deploy --quiet
  parent=$(git rev-parse refs/remotes/origin/deploy)
else
  parent=$source_sha
fi
timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
marker=$(printf 'Deploy source %s at %s\n' "$source_sha" "$timestamp" | git commit-tree "$tree_sha" -p "$parent")
git push origin "$marker:refs/heads/deploy"
echo "Deployment requested for main commit $source_sha."
echo "Track progress at https://github.com/rorygleeson/afl-video-platform/actions"
