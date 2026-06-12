#!/usr/bin/env bash
# Workspace materialization.
set -euo pipefail
BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)"

workspace_init() {
    local run_dir="$1" cell_id="$2"
    local ws="$run_dir/ws"
    rm -rf "$ws"
    mkdir -p "$ws"
    python3 "$BENCH_DIR/fixtures/gen_fixture.py" --output-dir "$ws"
    git -C "$ws" init -q
    git -C "$ws" config user.email "bench@localhost"
    git -C "$ws" config user.name "BenchBot"
    git -C "$ws" add -A
    git -C "$ws" commit -q -m "bench: initial orbital fixture ($cell_id)"
    echo "[ws] initialized: $ws"
}
