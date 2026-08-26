#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    printf 'usage: %s <repository> <workflow>\n' "$0" >&2
    exit 2
fi

: "${SHA:?SHA is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

repository=$1
workflow=$2
max_attempts=${RCB_ARTIFACT_LOOKUP_ATTEMPTS:-60}
interval=${RCB_ARTIFACT_LOOKUP_INTERVAL:-10}

if [[ ! $max_attempts =~ ^[1-9][0-9]*$ ]]; then
    printf 'artifact lookup attempts must be a positive integer\n' >&2
    exit 2
fi

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    run_info=$(gh run list \
        --repo "$repository" \
        --workflow "$workflow" \
        --commit "$SHA" \
        --event workflow_run \
        --limit 100 \
        --json databaseId,status,conclusion,createdAt \
        --jq 'sort_by(.createdAt) | .[-1] | select(. != null) | [.databaseId, .status, (.conclusion // "")] | @tsv')

    if [[ -n $run_info ]]; then
        IFS=$'\t' read -r run_id status conclusion <<<"$run_info"
        if [[ $status == completed ]]; then
            if [[ $conclusion == success ]]; then
                printf 'run_id=%s\n' "$run_id" >>"$GITHUB_OUTPUT"
                exit 0
            fi
            printf '::error::Build run %s for commit %s completed with conclusion %s.\n' \
                "$run_id" "$SHA" "${conclusion:-unknown}" >&2
            exit 1
        fi
    fi

    if (( attempt < max_attempts )); then
        sleep "$interval"
    fi
done

printf '::error::Timed out waiting for a successful build for commit %s.\n' "$SHA" >&2
exit 1
