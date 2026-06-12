#!/usr/bin/env bash
# skyline-benchmarks: cross-model blind benchmark runner
# Usage: ./run.sh <subcommand> [options]
# Subcommands: preflight | smoke | prep | score | all | report
#
# Requirements:
#   claude   -- claude CLI (https://claude.ai/download)
#   skyline  -- skyline binary (https://skyline.skylence.com) for Arm-B
#   python3  -- stdlib only
#   git      -- workspace initialization
#
# Configuration (env vars):
#   SKYLINE_BIN   path to skyline binary (default: skyline from PATH)

set -euo pipefail
BENCH_DIR="$(cd "$(dirname "$0")"; pwd)"
SKYLINE_BIN="${SKYLINE_BIN:-skyline}"

# shellcheck source=lib/daemon.sh
source "$BENCH_DIR/lib/daemon.sh"
# shellcheck source=lib/workspace.sh
source "$BENCH_DIR/lib/workspace.sh"

cmd="${1:-help}"; shift || true

# -- helpers ------------------------------------------------------------------

_mcp_cfg_arm_b() {
    local port="$1"
    local url; url="$(mcp_url "$port")"
    printf '{"mcpServers":{"skyline":{"type":"http","url":"%s"}}}' "$url"
}

_render_prompt() {
    local template_file="$1" cell_id="$2" ws="${3:-}"
    sed -e "s/{CELL_ID}/$cell_id/g" -e "s|{WS}|$ws|g" "$template_file"
}

# Run claude headless with --output-format json.
# $1=model  $2=mcp_cfg JSON  $3=disallowed (comma-sep, empty=none)
# $4=prompt  $5=add_dir (optional, for Arm-A workspace access)
# Always exits 0; failure emits {"is_error":true,"result":"RUNNER_ERROR","usage":{}}
_run_claude() {
    local model="$1" mcp_cfg="$2" disallowed="$3" prompt="$4" add_dir="${5:-}"
    local extra_flags=()
    [[ -n "$disallowed" ]] && extra_flags+=(--disallowedTools "$disallowed")
    [[ -n "$add_dir" ]]    && extra_flags+=(--add-dir "$add_dir")
    claude --model "$model" \
           --output-format json \
           --strict-mcp-config \
           --mcp-config "$mcp_cfg" \
           --dangerously-skip-permissions \
           ${extra_flags[@]+"${extra_flags[@]}"} \
           -p "$prompt" 2>/dev/null \
        || printf '{"is_error":true,"result":"RUNNER_ERROR","usage":{}}'
}

# Extract the text result field from a _run_claude JSON blob.
_claude_text() {
    python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('result',''))" "$1" 2>/dev/null || true
}

# Extract a usage integer field from a _run_claude JSON blob.
# $1=json_blob  $2=field_name (e.g. input_tokens, output_tokens)
_claude_usage() {
    python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('usage',{}).get(sys.argv[2],0))" "$1" "$2" 2>/dev/null || echo 0
}

# -- preflight ----------------------------------------------------------------

run_preflight() {
    local run_id="preflight-$(date -u +%Y%m%dT%H%M%S)"
    local rt="$BENCH_DIR/.runtime/$run_id"
    mkdir -p "$rt"

    echo "=== PREFLIGHT: $run_id ==="
    echo "SKYLINE_BIN=$SKYLINE_BIN"
    echo ""

    # -- Cell 1: Arm A (control) ----------------------------------------------
    echo "--- Cell 1: Claude Arm A (control, no MCP) ---"
    local c1
    c1=$(_run_claude \
        "claude-haiku-4-5-20251001" \
        '{"mcpServers":{}}' \
        "" \
        "Reply exactly: DONE-preflight-A and nothing else.")
    if _claude_text "$c1" | grep -qF "DONE-preflight-A"; then
        echo "PASS Cell 1 (Arm A): sentinel found"
        echo "  tokens in=$(_claude_usage "$c1" input_tokens) out=$(_claude_usage "$c1" output_tokens)"
    else
        echo "FAIL Cell 1 (Arm A): got: $(_claude_text "$c1")"; return 1
    fi

    # -- Cell 2: Arm B (skyline) ----------------------------------------------
    echo ""
    echo "--- Cell 2: Claude Arm B (skyline only) ---"
    local port; port="$(alloc_port)"
    local d2="$rt/cell-B-data"
    mkdir -p "$d2"
    daemon_start "$port" "preflight-B" "$d2"
    local cfg_b; cfg_b="$(_mcp_cfg_arm_b "$port")"
    local c2
    c2=$(_run_claude \
        "claude-haiku-4-5-20251001" \
        "$cfg_b" \
        "Read,Edit,Write,NotebookEdit,Grep,Glob,Bash" \
        "Call skyline_version and reply: DONE-preflight-B") || true
    daemon_stop "$port"
    if _claude_text "$c2" | grep -qF "DONE-preflight-B"; then
        echo "PASS Cell 2 (Arm B): sentinel + skyline MCP live"
        echo "  tokens in=$(_claude_usage "$c2" input_tokens) out=$(_claude_usage "$c2" output_tokens)"
    else
        echo "FAIL Cell 2 (Arm B): got: $(_claude_text "$c2")"; return 1
    fi

    # -- Cells 3 & 4: Codex (deferred) ----------------------------------------
    echo ""
    echo "--- Cells 3 & 4: Codex DEFERRED ---"
    echo "  TODO(probe-a): verify codex-spark model slug in models.json"
    echo "  TODO(probe-c): confirm guide-gate unlock from Codex arm"
    echo "  Run: codex exec --json ... to exercise Codex preflight cells"

    echo ""
    echo "=== PREFLIGHT PASS (cells 1-2) run_id=$run_id ==="
}

# -- prep ---------------------------------------------------------------------

run_prep() {
    local run_id="" cell_id=""
    while [[ $# -gt 0 ]]; do
        case "$1" in --run) run_id="$2"; shift 2 ;; --cell) cell_id="$2"; shift 2 ;; *) shift ;; esac
    done
    [[ -z "$run_id" || -z "$cell_id" ]] && { echo "Usage: prep --run RUN --cell CELL" >&2; return 1; }

    local cell_dir="$BENCH_DIR/.runtime/$run_id/$cell_id"
    mkdir -p "$cell_dir"

    workspace_init "$cell_dir" "$cell_id"

    printf '{"run_id":"%s","cell_id":"%s","ws":"%s/ws"}\n' \
        "$run_id" "$cell_id" "$cell_dir" > "$cell_dir/cell.json"
    echo "[prep] cell ready: $cell_dir"
}

# -- score --------------------------------------------------------------------

run_score() {
    local run_id="" cell_id="" task="t1"
    while [[ $# -gt 0 ]]; do
        case "$1" in --run) run_id="$2"; shift 2 ;; --cell) cell_id="$2"; shift 2 ;;
                     --task) task="$2"; shift 2 ;; *) shift ;; esac
    done
    [[ -z "$run_id" || -z "$cell_id" ]] && { echo "Usage: score --run RUN --cell CELL [--task T]" >&2; return 1; }

    local cell_dir="$BENCH_DIR/.runtime/$run_id/$cell_id"
    local manifest="$BENCH_DIR/fixtures/manifests/${task}.expected.sha256"

    python3 "$BENCH_DIR/lib/validate.py" \
        --ws "$cell_dir/ws" \
        --manifest "$manifest" \
        --output "$cell_dir/validate.json"

    python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('$cell_dir/validate.json').read_text())
print(f\"[score] correct={d['correct']} files_ok={d['files_ok']}/{d['files_total']}\")
"
}

# -- smoke --------------------------------------------------------------------

run_smoke() {
    local model="claude-haiku-4-5-20251001"
    [[ $# -gt 0 ]] && model="$1"

    local run_id="smoke-$(date -u +%Y%m%dT%H%M%S)"
    local rt="$BENCH_DIR/.runtime/$run_id"
    mkdir -p "$rt"

    echo "=== SMOKE: $run_id model=$model ==="

    # -- Arm A smoke ----------------------------------------------------------
    echo ""
    echo "--- Smoke Arm A: T1, $model, built-in tools only ---"
    workspace_init "$rt/cell-A" "smoke-A"
    local ws_a="$rt/cell-A/ws"
    local pre_a t1a
    pre_a="$(_render_prompt "$BENCH_DIR/prompts/preamble_A.txt" "smoke-A" "$ws_a")"
    t1a="$(_render_prompt "$BENCH_DIR/prompts/t1.md" "smoke-A" "")"
    local prompt_a="$pre_a
$t1a"

    echo "Spawning Claude Arm A (may take 2-5 min)..."
    local out_a_json out_a_text
    out_a_json=$(_run_claude "$model" '{"mcpServers":{}}' "" "$prompt_a" "$ws_a")
    out_a_text=$(_claude_text "$out_a_json")

    echo "Arm A response (last 5 lines):"
    echo "$out_a_text" | tail -5
    echo "Arm A tokens: in=$(_claude_usage "$out_a_json" input_tokens) out=$(_claude_usage "$out_a_json" output_tokens)"

    if echo "$out_a_text" | grep -qF "DONE-smoke-A"; then
        echo "Arm A sentinel: PASS"
        if python3 "$BENCH_DIR/lib/validate.py" --ws "$ws_a" \
            --manifest "$BENCH_DIR/fixtures/manifests/t1.expected.sha256" \
            --output "$rt/cell-A/validate.json" 2>/dev/null; then
            echo "Arm A validate: PASS"
        else
            echo "Arm A validate: FAIL"
        fi
        cat "$rt/cell-A/validate.json" 2>/dev/null || true
    else
        echo "Arm A sentinel: MISSING"
    fi

    # -- Arm B smoke ----------------------------------------------------------
    echo ""
    echo "--- Smoke Arm B: T1, $model, skyline MCP only ---"
    workspace_init "$rt/cell-B" "smoke-B"
    local ws_b="$rt/cell-B/ws"
    local port; port="$(alloc_port)"
    local d_b="$rt/cell-B/skyline-data"
    mkdir -p "$d_b"
    daemon_start "$port" "smoke-B" "$d_b"
    local cfg_b; cfg_b="$(_mcp_cfg_arm_b "$port")"
    local pre_b t1b
    pre_b="$(_render_prompt "$BENCH_DIR/prompts/preamble_B.txt" "smoke-B" "$ws_b")"
    t1b="$(_render_prompt "$BENCH_DIR/prompts/t1.md" "smoke-B" "")"
    local prompt_b="$pre_b
$t1b"

    echo "Spawning Claude Arm B (may take 2-5 min)..."
    local out_b_json out_b_text
    out_b_json=$(_run_claude "$model" "$cfg_b" "Read,Edit,Write,NotebookEdit,Grep,Glob,Bash" "$prompt_b" "$ws_b")
    daemon_stop "$port"
    out_b_text=$(_claude_text "$out_b_json")

    echo "Arm B response (last 5 lines):"
    echo "$out_b_text" | tail -5
    echo "Arm B tokens: in=$(_claude_usage "$out_b_json" input_tokens) out=$(_claude_usage "$out_b_json" output_tokens)"

    if echo "$out_b_text" | grep -qF "DONE-smoke-B"; then
        echo "Arm B sentinel: PASS"
        if python3 "$BENCH_DIR/lib/validate.py" --ws "$ws_b" \
            --manifest "$BENCH_DIR/fixtures/manifests/t1.expected.sha256" \
            --output "$rt/cell-B/validate.json" 2>/dev/null; then
            echo "Arm B validate: PASS"
        else
            echo "Arm B validate: FAIL"
        fi
        cat "$rt/cell-B/validate.json" 2>/dev/null || true
    else
        echo "Arm B sentinel: MISSING"
    fi

    echo ""
    echo "=== SMOKE DONE run_id=$run_id ==="
}

# -- dispatch -----------------------------------------------------------------

case "$cmd" in
    preflight) run_preflight "$@" ;;
    prep)      run_prep      "$@" ;;
    score)     run_score     "$@" ;;
    smoke)     run_smoke     "$@" ;;
    all)       run_preflight && run_smoke ;;
    report)    python3 "$BENCH_DIR/collate.py" --run-id "${1:-missing}" \
                       --history "$BENCH_DIR/results/history.jsonl" ;;
    help|*)
        echo "Usage: $0 {preflight|prep|score|smoke|all|report}"
        echo "  preflight  run isolation + connectivity checks"
        echo "  prep       materialize workspace for a cell (--run R --cell C)"
        echo "  score      validate workspace against manifest (--run R --cell C [--task T])"
        echo "  smoke      one model, T1, both arms A & B (proves end-to-end pipeline)"
        echo "  all        preflight + smoke"
        echo "  report     render results/history.jsonl"
        echo ""
        echo "Env vars: SKYLINE_BIN (default: skyline from PATH)"
        ;;
esac
