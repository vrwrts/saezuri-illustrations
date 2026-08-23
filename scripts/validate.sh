#!/usr/bin/env bash
# Lint bird cutout PNGs before they land in illustrations/. Run locally, and in CI
# by lint-illustrations.yml on the files a PR changes. This is a LINT for honest
# mistakes, not a security gate — the real trust boundary is human PR review
# (see CONTRIBUTING.md).
#
# Usage:
#   scripts/validate.sh illustrations              # lint every PNG in the folder
#   scripts/validate.sh illustrations/a.png b.png  # lint specific files
#   scripts/validate.sh --tsv <png-or-dir>...      # machine output for CI (see below)
#
# Default output is human-readable (✓/⚠/✗ + a summary). With --tsv, prints one
#   status<TAB>filename<TAB>message
# line per image (status = pass|warn|fail; message empty for pass) — the CI job turns
# that into the PR-comment table. Either mode exits non-zero if any image fails.
#
# Checks each PNG: basename is a valid slug (slug or slug-2, never underscore-prefixed),
# both poses of the species are present in the same folder (<slug>.png and <slug>-2.png),
# Pillow-decodable, and within a sane pixel cap (decompression-bomb guard, matching the
# pipeline's build_masks). A fully-opaque image is a warning — cutouts should be transparent.
set -euo pipefail

mode=human
if [ "${1:-}" = "--tsv" ]; then
    mode=tsv
    shift
fi
[ "$#" -ge 1 ] || { echo "usage: validate.sh [--tsv] <png-or-dir>..." >&2; exit 2; }

# Expand any directory args to their flat *.png contents.
files=()
for arg in "$@"; do
    if [ -d "$arg" ]; then
        for p in "$arg"/*.png; do [ -e "$p" ] && files+=("$p"); done
    else
        files+=("$arg")
    fi
done
if [ "${#files[@]}" -eq 0 ]; then
    [ "$mode" = tsv ] || echo "no PNGs to validate"
    exit 0
fi

python3 - "$mode" "${files[@]}" <<'PY'
import re, sys
from pathlib import Path

# Matches the pipeline's build_masks MAX_PIXELS (40 Mpx) — real bird cutouts are a few Mpx.
MAX_PIXELS = 40_000_000
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:-2)?\.png")
mode = sys.argv[1]

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
except ModuleNotFoundError:
    print("error: Pillow not installed (pip install -r pipeline/requirements.txt)", file=sys.stderr)
    sys.exit(2)


def check(p: Path):
    """Return (status, message) where status is pass | warn | fail."""
    name = p.name
    if name.startswith("_") or not NAME.fullmatch(name):
        return "fail", "invalid name (want <slug>.png or <slug>-2.png, [a-z0-9-] only)"
    # Both poses must ship together. Resolve against the folder on disk, not the argument
    # list, so a PR adding one pose next to an existing counterpart still passes.
    slug = p.stem.removesuffix("-2")
    for counterpart in (f"{slug}.png", f"{slug}-2.png"):
        if not (p.parent / counterpart).exists():
            return "fail", f"missing counterpart {counterpart}"
    try:
        with Image.open(p) as im:
            w, h = im.size
            if w * h > MAX_PIXELS:
                return "fail", f"{w}x{h} exceeds the {MAX_PIXELS}px cap"
            im.load()  # force decode so truncated/corrupt files fail here
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    except Exception as e:  # noqa: BLE001 - any decode failure is a lint failure
        return "fail", f"not a readable image ({e})"
    if not has_alpha:
        return "warn", "no alpha channel — cutouts should have a transparent background"
    return "pass", ""


errors = warnings = 0
for a in sys.argv[2:]:
    p = Path(a)
    status, msg = check(p)
    if status == "fail":
        errors += 1
    elif status == "warn":
        warnings += 1

    if mode == "tsv":
        print(f"{status}\t{p.name}\t{msg}")
    else:
        icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}[status]
        line = f"  {icon} {p.name}" + (f": {msg}" if msg else "")
        print(line, file=sys.stderr if status == "fail" else sys.stdout)

if mode != "tsv":
    print(f"\n{len(sys.argv) - 2} checked, {errors} error(s), {warnings} warning(s)")
sys.exit(1 if errors else 0)
PY
