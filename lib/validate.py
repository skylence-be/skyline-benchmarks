#!/usr/bin/env python3
"""Validate benchmark workspace against expected sha256 manifest + git porcelain."""
import argparse, hashlib, json, pathlib, subprocess, sys

def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def load_manifest(path: pathlib.Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            digest, relpath = parts
            result[relpath.lstrip("./")] = digest
    return result

def validate(ws: pathlib.Path, manifest: dict[str, str]) -> dict:
    ok = total = 0
    mismatches = []; missing = []
    for relpath, expected in manifest.items():
        total += 1
        abs_p = ws / relpath
        if not abs_p.exists(): missing.append(relpath); continue
        actual = _sha256(abs_p)
        if actual == expected: ok += 1
        else: mismatches.append(dict(file=relpath, expected=expected[:12], actual=actual[:12]))
    r = subprocess.run(["git", "-C", str(ws), "status", "--porcelain"],
                       capture_output=True, text=True)
    unexpected = []
    for ln in r.stdout.splitlines():
        if not ln.strip(): continue
        st = ln[:2].strip()   # XY status (fixed-width, must NOT strip ln first)
        fname = ln[3:].strip()
        if fname not in manifest: unexpected.append(dict(status=st, file=fname))
    correct = (ok == total and not missing)
    return dict(correct=correct, files_ok=ok, files_total=total,
                mismatches=mismatches, missing=missing,
                unexpected_changes=unexpected, unexpected_count=len(unexpected))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    manifest = load_manifest(pathlib.Path(args.manifest))
    result = validate(pathlib.Path(args.ws).resolve(), manifest)
    out = json.dumps(result, indent=2)
    pathlib.Path(args.output).write_text(out) if args.output else print(out)
    sys.exit(0 if result["correct"] else 1)

if __name__ == "__main__": main()
