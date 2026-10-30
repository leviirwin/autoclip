#!/usr/bin/env bash
# Commit files into <dir>/ on a data-only branch (created empty if it doesn't exist).
# Usage: tools/save_to_branch.sh <branch> <dir> <message> <file>...
# SAVE_REMOTE (a remote name or URL, default origin) saves to another repo instead.
set -euo pipefail
branch=$1 dir=$2 msg=$3; shift 3
remote=${SAVE_REMOTE:-origin}

save() {
wt=$(mktemp -d)
if git ls-remote --exit-code --heads "$remote" "$branch" >/dev/null; then
  git fetch -q --depth 1 "$remote" "$branch"
  git worktree add -q "$wt" FETCH_HEAD
else
  git worktree add -q --detach "$wt"
  git -C "$wt" checkout -q --orphan "$branch"
  git -C "$wt" rm -rfq --ignore-unmatch .
fi
mkdir -p "$wt/$dir"
cp "$@" "$wt/$dir/"
git -C "$wt" add "$dir"
if git -C "$wt" diff --cached --quiet; then
  git worktree remove --force "$wt"
  return 0
fi
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  git -C "$wt" -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -qm "$msg"
else
  git -C "$wt" commit -qm "$msg"
fi
ok=0
git -C "$wt" push -q "$remote" "HEAD:refs/heads/$branch" && ok=1
git worktree remove --force "$wt"
[ $ok = 1 ]
}

# Several runs can save at once (batched renders); on a rejected push, start again from
# the new branch tip after a random wait so the runs don't collide again.
for attempt in $(seq 1 12); do
  save "$@" && exit 0
  sleep $((RANDOM % 20 + attempt * 3))
done
echo "could not push to the $branch branch" >&2
exit 1
