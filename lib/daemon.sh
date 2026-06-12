#!/usr/bin/env bash
# Daemon lifecycle helpers. source this file, then call daemon_start/daemon_stop.
set -euo pipefail
SKYLINE_BIN="${SKYLINE_BIN:-skyline}"

daemon_start() {
    local port="$1" cell_id="$2" data_dir="$3"
    mkdir -p "$data_dir"
    SKYLINE_DATA_DIR="$data_dir" SKYLINE_BENCH=1 SKYLINE_BENCH_LABEL="$cell_id" \
        "$SKYLINE_BIN" daemon start --port "$port"
    local deadline=$(( $(date +%s) + 20 ))
    while ! "$SKYLINE_BIN" version --port "$port" &>/dev/null 2>&1; do
        [[ $(date +%s) -ge $deadline ]] && { echo "[daemon] start timeout port=$port" >&2; return 1; }
        sleep 0.5
    done
    echo "[daemon] started port=$port label=$cell_id"
}

daemon_stop() {
    local port="$1"
    "$SKYLINE_BIN" daemon stop --port "$port" 2>/dev/null || true
    echo "[daemon] stopped port=$port"
}

mcp_url() { echo "http://127.0.0.1:${1}/mcp"; }

is_port_free() {
    ! lsof -i ":${1}" -sTCP:LISTEN -t &>/dev/null
}

alloc_port() {
    local p
    for p in $(seq 43001 43099); do
        is_port_free "$p" && { echo "$p"; return 0; }
    done
    echo "[daemon] no free port in 43001-43099" >&2; return 1
}
