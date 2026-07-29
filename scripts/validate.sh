#!/usr/bin/env bash
# Lint bird cutout PNGs before they land in illustrations/. Run locally, and in CI
# by validate-pr.yml on the files a PR changes. This is a LINT for honest mistakes,
# not a security gate — the real trust boundary is human PR review (see CONTRIBUTING.md).
#
# Usage:
#   scripts/validate.sh illustrations              # lint every PNG in the folder
#   scripts/validate.sh illustrations/a.png b.png  # lint specific files
#
# Checks each PNG: basename is a valid slug (slug or slug-2, never underscore-prefixed),
# Pillow-decodable, and within a sane pixel cap (decompression-bomb guard, matching the
# pipeline's build_masks). Warns (does not fail) on a fully-opaque image — cutouts should
# have a transparent background.
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: validate.sh <png-or-dir>..." >&2; exit 2; }

# Expand any directory args to their flat *.png contents.
files=()
for arg in "$@"; do
    if [ -d "$arg" ]; then
        for p in "$arg"/*.png; do [ -e "$p" ] && files+=("$p"); done
    else
        files+=("$arg")
    fi
done
[ "${#files[@]}" -ge 1 ] || { echo "no PNGs to validate"; exit 0; }

python3 - "${files[@]}" <<'PY'
import re, sys
from pathlib import Path

# Matches the pipeline's build_masks MAX_PIXELS (40 Mpx) — real bird cutouts are a few Mpx.
MAX_PIXELS = 40_000_000
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:-2)?\.png")

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
except ModuleNotFoundError:
    print("error: Pillow not installed (pip install -r pipeline/requirements.txt)", file=sys.stderr)
    sys.exit(2)

errors = warnings = 0
for a in sys.argv[1:]:
    p = Path(a)
    name = p.name
    if name.startswith("_") or not NAME.fullmatch(name):
        print(f"  ✗ {name}: invalid name (want <slug>.png or <slug>-2.png, [a-z0-9-] only)", file=sys.stderr)
        errors += 1
        continue
    try:
        with Image.open(p) as im:
            w, h = im.size
            if w * h > MAX_PIXELS:
                print(f"  ✗ {name}: {w}x{h} exceeds the {MAX_PIXELS}px cap", file=sys.stderr)
                errors += 1
                continue
            im.load()  # force decode so truncated/corrupt files fail here
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        if not has_alpha:
            print(f"  ⚠ {name}: no alpha channel — cutouts should have a transparent background")
            warnings += 1
        else:
            print(f"  ✓ {name}")
    except Exception as e:  # noqa: BLE001 - any decode failure is a lint failure
        print(f"  ✗ {name}: not a readable image ({e})", file=sys.stderr)
        errors += 1

print(f"\n{len(sys.argv)-1} checked, {errors} error(s), {warnings} warning(s)")
sys.exit(1 if errors else 0)
PY
