#!/usr/bin/env bash
# Prints the image tag for one Dockerfile: a hash of the files that make up that image.
# The tag only changes when one of them changes, so unchanged images keep their tag.
set -euo pipefail
dockerfile="${1:?usage: scripts/image_tag.sh containers/Dockerfile.<name>}"
cd "$(git rev-parse --show-toplevel)"

# Code folders each image depends on. Update this when a worker starts using another folder.
case "$(basename "$dockerfile")" in
    Dockerfile.documents) code=(backend/documents backend/profiles backend/utils) ;;
    Dockerfile.seed | Dockerfile.scrape | Dockerfile.classify) code=(backend/jobs backend/utils) ;;
    *) echo "No code folders listed for $dockerfile in $0" >&2; exit 1 ;;
esac

git ls-files -z -- "$dockerfile" .dockerignore pyproject.toml uv.lock "${code[@]}" ':!*.md' \
    | xargs -0 sha256sum | sha256sum | cut -c1-40
