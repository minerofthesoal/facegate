#!/usr/bin/env bash
set -e
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
curl -fsSL https://github.com/minerofthesoal/visagate/archive/refs/heads/main.tar.gz | tar xz -C "$tmpdir" --strip-components=1
cd "$tmpdir"
exec sudo ./install.sh
