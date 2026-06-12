#!/usr/bin/env python3
"""Extract skyline bench stream for a labeled cell."""
import argparse, json, os, pathlib, subprocess

def extract(skyline_bin: str, data_dir: str, label: str) -> dict:
    env = {**os.environ, "SKYLINE_DATA_DIR": data_dir}
    r = subprocess.run([skyline_bin, "bench", "report", "--label", label, "--json"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return dict(error=r.stderr.strip(), ops=0, input_bytes=0, output_bytes=0, dur_us_total=0)
    rows = []
    for line in r.stdout.strip().splitlines():
        try: rows.append(json.loads(line))
        except: pass
    if not rows:
        try: rows = json.loads(r.stdout)
        except: pass
    if isinstance(rows, list):
        return dict(ops=len(rows),
                    input_bytes=sum(x.get("input_bytes", 0) for x in rows),
                    output_bytes=sum(x.get("output_bytes", 0) for x in rows),
                    dur_us_total=sum(x.get("dur_us", 0) for x in rows),
                    rows=rows)
    return dict(ops=0, input_bytes=0, output_bytes=0, dur_us_total=0, raw=str(rows))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skyline-bin", default="skyline")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    result = extract(args.skyline_bin, args.data_dir, args.label)
    out = json.dumps(result, indent=2)
    pathlib.Path(args.output).write_text(out) if args.output else print(out)

if __name__ == "__main__": main()
