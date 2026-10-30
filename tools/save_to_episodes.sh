#!/usr/bin/env bash
# Commit files into episodes/<name>/ on the data-only `episodes` branch.
# Usage: tools/save_to_episodes.sh <name> <message> <file>...
set -euo pipefail
name=$1 msg=$2; shift 2
exec "$(dirname "$0")/save_to_branch.sh" episodes "episodes/$name" "$msg" "$@"
