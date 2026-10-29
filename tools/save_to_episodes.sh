#!/usr/bin/env bash
# Commit files into episodes/<name>/ on the data-only `episodes` branch.
# Usage: tools/save_to_episodes.sh <name> <message> <file>...
set -euo pipefail
name=$1 msg=$2; shift 2

save() {
wt=$(mktemp -d)
if git ls-remote --exit-code --heads origin episodes >/dev/null; then
  git fetch -q --depth 1 origin episodes
  git worktree add -q "$wt" FETCH_HEAD
else
  git worktree add -q --detach "$wt"
  git -C "$wt" checkout -q --orphan episodes
  git -C "$wt" rm -rfq --ignore-unmatch .
fi
mkdir -p "$wt/episodes/$name"
cp "$@" "$wt/episodes/$name/"
git -C "$wt" add "episodes/$name"
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  git -C "$wt" -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -qm "$msg"
else
  git -C "$wt" commit -qm "$msg"
fi
ok=0
git -C "$wt" push -q origin HEAD:refs/heads/episodes && ok=1
git worktree remove --force "$wt"
[ $ok = 1 ]
}

# Two runs can save at once; on a rejected push, start again from the new branch tip.
for attempt in 1 2 3 4 5; do
  save "$@" && exit 0
  sleep $((attempt * 5))
done
echo "could not push to the episodes branch" >&2
exit 1
