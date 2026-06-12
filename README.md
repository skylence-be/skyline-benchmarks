# skyline-benchmarks

Blind cross-model benchmark comparing AI coding agents on synthetic Rust refactoring tasks
using **built-in file tools** (Arm A, control) versus **skyline MCP tools** (Arm B, treatment).

## What it measures

Three measurement planes per cell:

| Plane | Source | What it captures |
|-------|--------|------------------|
| 1 | `claude --output-format json` stdout | input_tokens, cache tokens, output_tokens |
| 2 | Transcript payload bytes | bytes sent to tools / returned from tools |
| 3 | `skyline bench report` (Arm B only) | op count, input/output bytes, latency per call |

Four tasks of increasing difficulty:

| Task | Description | Sites |
|------|-------------|-------|
| T0 | No-op baseline | — |
| T1 | Multi-site semantic rename — `normalize_path` → `canonicalize_path`, avoid method decoys | 19 |
| T2 | Repo-wide structural rewrite — `track::event` → `track::event_v2(…, Flags::default())` | 47 |
| T3 | Precision edit in large files — change `MAX_RETRIES` in `pub mod prod` only, 8 files × ~3200 lines | 8 |

Correctness is verified against pre-committed sha256 manifests of the expected post-task state.

## Requirements

- **[Claude CLI](https://claude.ai/download)** — `claude` on PATH, logged in
- **[skyline](https://skyline.skylence.com)** — `skyline` on PATH (for Arm-B bench daemon)
- **python3** (stdlib only)
- **git** (workspace initialization)

## Installation

```sh
git clone https://github.com/skylence-be/skyline-benchmarks
cd skyline-benchmarks
```

Optionally set the skyline binary path:
```sh
export SKYLINE_BIN=/path/to/skyline
```

## Running

```sh
# Connectivity + isolation check (Claude arms A and B)
./run.sh preflight

# End-to-end smoke test: one model, T1, both arms
./run.sh smoke

# Use a specific model
./run.sh smoke claude-sonnet-4-6

# Preflight + smoke in one shot
./run.sh all

# Render a report from the result log
./run.sh report --run-id smoke-20260612T103800
```

## How it works

Each **cell** is a fresh agent session on a clean copy of the synthetic `orbital` Rust crate:

1. **Arm A (control)** — `claude -p --output-format json --strict-mcp-config --mcp-config '{"mcpServers":{}}'`  
   No MCP servers; agent uses built-in file tools (Read, Edit, Write, Bash, Grep, Glob).

2. **Arm B (treatment)** — same flags, but with a per-cell skyline bench daemon on a private port  
   `--mcp-config '{"mcpServers":{"skyline":{"type":"http","url":"..."}}}'`  
   `--disallowedTools Read,Edit,Write,...` hard-blocks built-ins; only skyline MCP is available.

A per-cell bench daemon records all skyline operations via `SKYLINE_BENCH=1 SKYLINE_BENCH_LABEL=<cell_id>`
and is torn down after scoring.

## Project structure

```
run.sh                     Main runner (headless, stdlib deps only)
models.json                7 model configurations (4 Claude, 3 Codex)
prompts/                   Task prompts + arm preambles
fixtures/
  gen_fixture.py           Deterministic synthetic Rust crate generator (no RNG)
  manifests/               Pre-committed expected sha256 manifests per task
lib/
  daemon.sh                Per-cell skyline bench daemon lifecycle helpers
  workspace.sh             Workspace materialization (gen_fixture.py → git init)
  validate.py              sha256 + git-porcelain correctness checker
  measure_skyline.py       Plane 3: skyline bench stream parser
  measure_codex.py         Codex rollout JSONL token extractor
collate.py                 Report renderer (reads results/history.jsonl)
results/
  history.jsonl            Append-only cell result log
```

## Caveats

1. **No cross-vendor token comparisons** — tokenizers and system prompts differ between Claude and Codex.
   Only within-model B/A deltas are valid. `payload_bytes` is the only cross-runtime raw measure.
2. **Arm B fixed overhead** — T0 adjusts for the schema/guide cost; the ~2.5k guide read is intentional.
3. **Enforcement asymmetry** — Claude Arm B is hard-enforced via `--disallowedTools`; Codex Arm B is
   instruction-enforced and compliance-checked post-hoc.
4. **Control heterogeneity** — Claude Arm A uses structured tools; Codex Arm A uses shell + `apply_patch`.
5. **Synthetic fixture, serial execution** — no claim on real repos, long sessions, or multi-agent scenarios.
   Treat deltas < 2× as noise. Agents are stochastic.
6. **CLAUDE.md hooks** — a hook that mandates specific tools in your environment may bias Arm A results.
   `--strict-mcp-config` controls MCP availability but does not suppress hook-injected system prompts.
7. **Model alias drift** — rows pin the model slug reported at run time, not the alias you requested.
