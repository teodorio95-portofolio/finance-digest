#!/usr/bin/env bash
# Commit everything under out/ (a worktree of the `data` branch) and push, only if it changed.
#   scripts/commit-data.sh "state: 2026-09-28"
set -euo pipefail
cd "${DATA_DIR:-out}"
git add -A
if git diff --cached --quiet; then
  echo "data: nothing to commit"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "$1"
git push -q origin HEAD:data
echo "data: pushed '$1'"
