#!/usr/bin/env bash
# Pull-model poller for ai-review findings exported from the Orakul repo.
#
# Walks recent GitHub Actions runs in the Orakul repository, looks for
# artifacts named `pr-review-export-<PR>`, and feeds each new artifact into
# `cod-doc ingest ai_review --from-pr <PR>`.  A state file records already
# processed run/PR pairs so the script is idempotent and safe to run from cron.
#
# Usage:
#   scripts/ingest-orakul-reviews.sh [--repo PATH] [--project PROJECT]
#       [--state FILE] [--limit N] [--dry-run]
#
# Environment variables:
#   REPO_DIR    default repository path (default: /Users/dakh/Git/_my/Mozarella/Orakul)
#   PROJECT     default cod-doc project slug (default: cod-doc)
#   STATE_FILE  default state file path (default: ~/.cache/cod-doc/orakul-reviews.state)
#   LIMIT       default number of recent runs to inspect (default: 50)

set -euo pipefail

DEFAULT_REPO_DIR="/Users/dakh/Git/_my/Mozarella/Orakul"
DEFAULT_PROJECT="cod-doc"
DEFAULT_STATE_FILE="${HOME}/.cache/cod-doc/orakul-reviews.state"
DEFAULT_LIMIT=50

repo_dir="${REPO_DIR:-$DEFAULT_REPO_DIR}"
project="${PROJECT:-$DEFAULT_PROJECT}"
state_file="${STATE_FILE:-$DEFAULT_STATE_FILE}"
limit="${LIMIT:-$DEFAULT_LIMIT}"
dry_run=0

usage() {
  cat <<EOF >&2
Usage: $0 [--repo PATH] [--project PROJECT] [--state FILE] [--limit N] [--dry-run]

  --repo PATH      Path to local Orakul Git repository (default: $DEFAULT_REPO_DIR)
  --project NAME   cod-doc project slug (default: $DEFAULT_PROJECT)
  --state FILE     File storing processed run/PR pairs (default: $DEFAULT_STATE_FILE)
  --limit N        Number of recent runs to inspect (default: $DEFAULT_LIMIT)
  --dry-run        Print actions without calling cod-doc or modifying state
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo_dir="$2"
      shift 2
      ;;
    --project)
      project="$2"
      shift 2
      ;;
    --state)
      state_file="$2"
      shift 2
      ;;
    --limit)
      limit="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: gh CLI not found. Install GitHub CLI to run this poller." >&2
  exit 1
fi

if ! command -v cod-doc >/dev/null 2>&1; then
  echo "Error: cod-doc CLI not found." >&2
  exit 1
fi

if [[ ! -d "$repo_dir" ]]; then
  echo "Error: repository directory does not exist: $repo_dir" >&2
  exit 1
fi

mkdir -p "$(dirname "$state_file")"
touch "$state_file"

process_artifact() {
  local run_id="$1"
  local artifact_name="$2"
  local pr_number
  local state_key

  if [[ "$artifact_name" =~ ^pr-review-export-([0-9]+)$ ]]; then
    pr_number="${BASH_REMATCH[1]}"
    state_key="${run_id}/${pr_number}"

    if grep -qx "$state_key" "$state_file" 2>/dev/null; then
      echo "Skip already processed: $state_key"
      return 0
    fi

    if [[ "$dry_run" -eq 1 ]]; then
      echo "Would ingest $artifact_name (run $run_id, PR $pr_number) into project '$project'"
      return 0
    fi

    echo "Ingesting $artifact_name (run $run_id, PR $pr_number) into project '$project'"
    cod-doc ingest ai_review --project "$project" --from-pr "$pr_number"
    echo "$state_key" >> "$state_file"
  fi
}

cd "$repo_dir"

repo_full=$(gh repo view --json nameWithOwner -q .nameWithOwner)

# shellcheck disable=SC2207
run_ids=(
  $(gh run list --json databaseId --limit "$limit" -q '.[].databaseId')
)

if [[ ${#run_ids[@]} -eq 0 ]]; then
  echo "No runs found."
  exit 0
fi

for run_id in "${run_ids[@]}"; do
  # shellcheck disable=SC2207
  artifact_names=(
    $(gh api "repos/${repo_full}/actions/runs/${run_id}/artifacts" -q '.artifacts[].name')
  )
  for artifact_name in "${artifact_names[@]}"; do
    process_artifact "$run_id" "$artifact_name"
  done
done
