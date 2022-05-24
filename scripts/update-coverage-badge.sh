#!/usr/bin/env bash

set -e

function main {
    local badge_branch="$1"
    local commit_args=( --amend --reset-author )
    if [ -z "$badge_branch" ]; then
        echo "USAGE: $0 <BRANCH>" >&2
        return 1
    fi
    local for_rev=$(git rev-parse --short HEAD)
    git remote -v
    git fetch origin "$badge_branch"
    git checkout -b "$badge_branch" "origin/${badge_branch}"
    mv -v .coverage.svg coverage.svg
    if git diff --no-patch --exit-code -- coverage.svg; then
        # file has not changed, nothing to commit/push
        echo "INFO: coverage badge has not changed"
        return 0
    fi
    # file has changed, commit it
    git add coverage.svg
    git commit "${commit_args[@]}" -m "Update coverage badge for $for_rev"
    git push -f origin "${badge_branch}:${badge_branch}"
}


main "$@"
