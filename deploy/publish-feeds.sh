#!/bin/sh
# Builds the feeds and publishes them to the "feeds" branch, which the GitLab push
# mirror copies to GitHub, where GitHub Pages serves it.
#
# The branch always holds a single commit (force-pushed), so old MP3s don't pile up
# in the history.
#
# Needs: PUSH_TOKEN (project access token with write_repository) and BASE_URL.
# FEEDS_REMOTE can override the push URL (used for local testing).
set -eu

FEEDS_BRANCH="${FEEDS_BRANCH:-feeds}"
OUT="${OUT:-public}"
if [ -z "${FEEDS_REMOTE:-}" ]; then
  FEEDS_REMOTE=$(echo "$CI_SERVER_URL" | sed "s#://#://oauth2:${PUSH_TOKEN}@#")/${CI_PROJECT_PATH}.git
fi

# Start from what's published, so bulletins that are already encoded are reused.
rm -rf "$OUT"
if git ls-remote --exit-code --heads "$FEEDS_REMOTE" "$FEEDS_BRANCH" > /dev/null 2>&1; then
  git clone --quiet --depth 1 --branch "$FEEDS_BRANCH" "$FEEDS_REMOTE" "$OUT"
else
  mkdir -p "$OUT"
  git -C "$OUT" init --quiet
  git -C "$OUT" remote add origin "$FEEDS_REMOTE"
fi

# Publish whatever succeeded even if a region failed, then report the failure.
status=0
${PYTHON:-python3} nrk_flash.py --out "$OUT" --base-url "$BASE_URL" -v || status=$?

cd "$OUT"
touch .nojekyll  # Serve the files as-is on GitHub Pages.
git add -A
if git rev-parse --verify --quiet HEAD > /dev/null && git diff --cached --quiet; then
  echo "No new bulletins."
  exit "$status"
fi

git -c user.name="NRK feed bot" -c user.email="feeds@noreply.invalid" \
  commit --quiet -m "Update feeds"
# Squash to a single commit so the branch never grows.
git checkout --quiet --orphan publish
git -c user.name="NRK feed bot" -c user.email="feeds@noreply.invalid" \
  commit --quiet -m "Feeds $(date -u +%Y-%m-%dT%H:%MZ)"
git push --quiet --force origin "publish:refs/heads/$FEEDS_BRANCH"
echo "Published to $FEEDS_BRANCH."
exit "$status"
