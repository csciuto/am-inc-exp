#!/bin/sh
# Writes docs/version.json — the only file generated at commit time. The site
# footer reads it to show which build is deployed (commit + date).
#
# The recorded hash is the *parent* commit: this runs before the new commit
# exists, so version.json in commit N points at commit N-1 (the commit this build
# is based on). That one-step lag is expected; see README "Versioning".
#
# Run automatically by the pre-commit hook (scripts/hooks/pre-commit), or by hand:
#     scripts/stamp-version.sh
set -eu

HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
DATE=$(date +%Y-%m-%d)
printf '{"commit":"%s","date":"%s"}\n' "$HASH" "$DATE" > docs/version.json
