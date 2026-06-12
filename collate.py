#!/usr/bin/env python3
"""Render results/history.jsonl into a report.md matrix.

Usage: python3 collate.py --run-id <run_id> [--output results/reports/<id>-report.md]
"""
import argparse, json, pathlib, statistics
from collections import defaultdict

CAVEATS = """
## Caveats (print in every report)

1. No cross-vendor token comparisons: different tokenizers/system prompts. Only within-model B/A deltas.
   `payload_bytes` is the only cross-runtime raw measure.
2. Arm B carries skyline fixed overhead (T0-adjusts schema cost; ~2.5k guide read is intentional).
3. Enforcement asymmetry: Claude-B hard-enforced, Codex-B instructed + compliance-checked.
4. Control heterogeneity: Claude-A structured tools vs Codex-A shell+apply_patch.
5. in_total != cost (uncached/cache-read split carried for later pricing).
6. Synthetic fixture, 3 tasks, one machine, serial — no claim on real repos/long sessions/multi-agent.
   Treat <2x deltas as noise. Stochastic agents.
7. Model aliases drift — rows pin reported slugs.
"""

def _med(vals):
    if not vals: return None
    return statistics.median(vals)

def render_report(rows: list[dict], run_id: str) -> str:
    cells = defaultdict(list)
    for r in rows:
        if r.get("run_id") != run_id: continue
        key = (r["model"], r["task"], r["arm"])
        cells[key].append(r)

    tasks = sorted(set(r["task"] for r in rows if r.get("run_id") == run_id))
    models = sorted(set(r["model"] for r in rows if r.get("run_id") == run_id))

    lines = [f"# Cross-model bench report: {run_id}", ""]
    for task in tasks:
        lines.append(f"## Task {task}")
        lines.append("")
        lines.append("| model | arm | tokens_in | tokens_out | tool_calls | correct | wall_s |")
        lines.append("|---|---|---|---|---|---|---|")
        for model in models:
            for arm in ("A", "B"):
                reps = cells.get((model, task, arm), [])
                if not reps:
                    lines.append(f"| {model} | {arm} | – | – | – | – | – |")
                    continue
                tok_in = _med([r.get("tokens", {}).get("in_total", 0) for r in reps])
                tok_out = _med([r.get("tokens", {}).get("out", 0) for r in reps])
                calls = _med([r.get("tool", {}).get("tool_calls", 0) for r in reps])
                correct_n = sum(1 for r in reps if r.get("correct"))
                wall = _med([r.get("wall_secs_transcript", 0) for r in reps])
                lines.append(f"| {model} | {arm} | {tok_in} | {tok_out} | {calls} |"
                             f" {correct_n}/{len(reps)} | {wall:.1f} |")
        lines.append("")
    lines.append(CAVEATS)
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--output", default=None)
    ap.add_argument("--history", default="results/history.jsonl")
    args = ap.parse_args()

    hist = pathlib.Path(args.history)
    rows = []
    if hist.exists():
        for line in hist.read_text().splitlines():
            try: rows.append(json.loads(line))
            except: pass

    report = render_report(rows, args.run_id)
    out_path = args.output or f"results/reports/{args.run_id}-report.md"
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out_path).write_text(report)
    print(f"report written: {out_path}")

if __name__ == "__main__": main()
