#!/usr/bin/env zsh

for dir in */; do
    if [[ -d "$dir" ]]; then
        echo "$dir"
        [ -z "$dir"/pyproject.toml ] && continue
        pushd "$dir"
        uvx migrate-to-uv
        popd
    fi
done
