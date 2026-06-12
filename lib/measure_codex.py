#!/usr/bin/env python3
"""Extract token usage from a Codex rollout JSONL.

Rollout path: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
Use last non-null info.total_token_usage; fallback to sum of token_count events.
"""
import argparse, json, pathlib

def _find_rollout(codex_home: str, session_id: str | None) -> pathlib.Path:
    base = pathlib.Path(codex_home) / "sessions"
    if not base.exists(): raise FileNotFoundError(f"sessions dir not found: {base}")
    if session_id:
        matches = list(base.rglob(f"*{session_id}*.jsonl"))
        if matches: return matches[0]
    files = sorted(base.rglob("rollout-*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files: raise FileNotFoundError(f"no rollout files under {base}")
    return files[0]

def extract(rollout_path: pathlib.Path) -> dict:
    last_total = None; model_slug = None
    token_events = []; tool_calls = payload_in = payload_out = 0
    with rollout_path.open() as f:
        for line in f:
            try: obj = json.loads(line)
            except: continue
            if "model" in obj and model_slug is None: model_slug = obj["model"]
            t = obj.get("type", "")
            if t == "token_count": token_events.append(obj)
            info = obj.get("info") or {}
            if isinstance(info, dict) and info.get("total_token_usage"):
                last_total = info["total_token_usage"]
            if t in ("function_call", "tool_call"):
                tool_calls += 1
                payload_in += len(json.dumps(obj.get("arguments", obj.get("input", {}))))
            elif t in ("function_call_output", "tool_result"):
                payload_out += len(json.dumps(obj.get("output", obj.get("content", ""))))
    if last_total and isinstance(last_total, dict):
        in_total = last_total.get("input_tokens", 0); out = last_total.get("output_tokens", 0)
    else:
        in_total = sum(e.get("input_tokens", 0) for e in token_events)
        out = sum(e.get("output_tokens", 0) for e in token_events)
    return dict(in_total=in_total, out=out, turns=len(token_events), tool_calls=tool_calls,
                payload_bytes_in=payload_in, payload_bytes_out=payload_out,
                model_slug=model_slug, rollout_file=str(rollout_path))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-home", required=True)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    path = _find_rollout(args.codex_home, args.session_id)
    result = extract(path)
    out = json.dumps(result, indent=2)
    pathlib.Path(args.output).write_text(out) if args.output else print(out)

if __name__ == "__main__": main()
