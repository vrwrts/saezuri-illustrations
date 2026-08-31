#!/usr/bin/env python3
"""Saezuri - illustration generator (one-shot).

Original Saezuri code (not ported from AvianVisitors). Invoked by the Node
refresh service (src/server/) as a subprocess: given the species that have been
heard but have no cutout yet, it generates kachO-e cutouts for them (by driving
the pipeline/ scripts) and rebuilds the layout manifest. The refresh service owns
the BirdNET-Go relationship and decides *what* is missing; this script only does
the generation, so a change to the art style or mask format stays a one-file fix
in pregen.py / matte.py / build_masks.py.

Generation reuses the existing scripts (pregen --matte -> build_masks). pregen
cuts the magenta ground out of each render in memory before writing it, so the
served directory only ever receives finished cutouts; --repair cleans up files
left half-done by older versions that matted in a second pass.

Only public read endpoints of BirdNET-Go are ever touched (by the service, not
here); nothing is written back to it.

Usage:
    # generate art for two species (both poses), then rebuild the manifest:
    python3 worker.py --generate "Turdus merula|Blackbird" "Parus major|Great tit"

    # one pose only, with an operator notes file layered over the bundled one -
    # how the refresh service calls this:
    python3 worker.py --generate "Turdus merula|Blackbird" --poses 1 \
        --notes /data/illustrations/_species-notes.json

    # re-render a pose after editing its note:
    python3 worker.py --generate "Turdus merula|Blackbird" --poses 1 --force

    # just (ensure fallback +) rebuild the manifest, no generation:
    python3 worker.py --rebuild

    # matte anything left opaque by an older pipeline, then rebuild:
    python3 worker.py --repair

Env:
    GENERATE_API_KEY required for --generate; unset => error (the service only
                     calls --generate when a key is set).
    GENERATE_API_URL OpenAI-compatible endpoint, GENERATE_MODEL the model. Read
                     by pregen itself; see imageapi.py for the defaults.
    GENERATE_SLEEP   seconds between image-API calls (passed to pregen --sleep).
                     Only bites when one invocation covers several images; the
                     refresh service asks for one pose at a time and paces the
                     calls itself.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import imageapi
# slugify is the canonical scientific-name -> filename join key, kept in parity
# with the frontend (src/domain/slug.ts) and the rest of the pipeline.
from pregen import BUNDLED_NOTES, slugify

HERE = Path(__file__).resolve().parent
PREGEN = HERE / "pregen.py"
MATTE = HERE / "matte.py"
BUILD_MASKS = HERE / "build_masks.py"
MAKE_FALLBACK = HERE / "make_fallback.py"

# Reference art bundled into the image (see pipeline/assets/). Missing dirs are
# fine - pregen degrades gracefully without them (lower style fidelity).
BUNDLED = HERE / "assets"
STYLES_DIR = BUNDLED / "styles"
ANTI_DIR = BUNDLED / "anti"

# In the container the served illustration dir; overridden for local dev.
DEFAULT_ASSETS_DIR = Path("/usr/share/nginx/html/assets/illustrations")
DEFAULT_CACHE_DIR = Path("/var/cache/saezuri")

# Perched (no suffix) and flight (-2) pose file suffixes.
POSE_SUFFIXES = ("", "-2")


def parse_species_args(items: list[str]) -> list[tuple[str, str]]:
    """Parse `"scientificName|commonName"` args into (sci, com) pairs. A missing
    common name defaults to the scientific name. Blank scientific names are
    dropped."""
    out: list[tuple[str, str]] = []
    for item in items:
        sci, _, com = item.partition("|")
        sci = sci.strip()
        com = (com or sci).strip()
        if sci:
            out.append((sci, com))
    return out


def _run(cmd: list[str], **kwargs) -> int:
    """Run a pipeline script, letting its stdout/stderr flow to the container
    log. Non-zero is returned, not raised: a partial run should not abort the
    whole batch (pregen/matte skip already-done work, so the next run retries)."""
    proc = subprocess.run([sys.executable, *cmd], check=False, **kwargs)
    return proc.returncode


def ensure_fallback(assets_dir: Path) -> None:
    fallback = assets_dir / "_fallback.png"
    if not fallback.exists():
        _run([str(MAKE_FALLBACK), "--out", str(fallback)])


def seed_anti_refs(refs_dir: Path) -> None:
    """Copy the bundled contrastive anti-reference photos into the writable refs
    cache so pregen finds them alongside the Wikipedia photos it fetches. No-op
    if none are bundled."""
    if not ANTI_DIR.is_dir():
        return
    for src in ANTI_DIR.glob("_anti_*.jpg"):
        dest = refs_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)


def rebuild_manifest(assets_dir: Path) -> None:
    _run([
        str(BUILD_MASKS),
        "--illustrations", str(assets_dir),
        "--fallback", str(assets_dir / "_fallback.png"),
        "--out", str(assets_dir.parent.parent / "layout-manifest.json"),
    ])


def generate(
    missing: list[tuple[str, str, str]],
    assets_dir: Path,
    refs_dir: Path,
    poses: list[int],
    notes_paths: list[Path],
    force: bool = False,
) -> None:
    """Render the requested species/poses as finished cutouts.

    pregen renders on the flat magenta ground and, with --matte, cuts it out in
    memory before writing - so the served directory only ever receives RGBA
    cutouts. Doing it there rather than in a second pass here is what keeps a
    killed run from stranding a magenta rectangle under a real filename (see
    --repair for cleaning up files left by older versions)."""
    args = [str(PREGEN), "--out", str(assets_dir), "--refs", str(refs_dir), "--matte"]
    if STYLES_DIR.is_dir():
        args += ["--styles", str(STYLES_DIR)]
    for notes_path in notes_paths:
        args += ["--notes", str(notes_path)]
    if force:
        args += ["--force"]
    # Throttle image-API calls to stay within the provider's rate limit.
    # Only bites when this invocation covers more than one image; the refresh
    # service calls us one pose at a time and paces the calls itself.
    sleep = os.environ.get("GENERATE_SLEEP", "").strip()
    if sleep:
        args += ["--sleep", sleep]
    for sci, com, _ in missing:
        args += ["--species", f"{sci}|{com}"]
    args += ["--poses", *[str(p) for p in poses]]
    # No env splice: pregen resolves the endpoint, key, and model from the same
    # environment this process already has.
    _run(args)


def repair(assets_dir: Path) -> int:
    """Matte any cutout that is still fully opaque, and report how many were
    rewritten.

    Older pipeline versions wrote the magenta render under its real filename and
    matted it in a second pass, so a run killed in between left a permanent
    magenta rectangle that nothing would ever revisit. region_cut skips files
    that already have transparency, so this is cheap to run over a whole
    directory."""
    candidates = [p for p in sorted(assets_dir.glob("*.png"))
                  if not p.stem.startswith("_")]
    repaired = 0
    for path in candidates:
        if _is_transparent(path):
            continue
        rc = _run([str(MATTE), "--region", str(path), "--out", str(path)])
        if rc != 0:
            print(f"saezuri-worker: repair FAILED for {path.name} (exit {rc})",
                  file=sys.stderr)
            continue
        repaired += 1
    print(f"saezuri-worker: repaired {repaired}/{len(candidates)} cutout(s)")
    return repaired


def _is_transparent(path: Path) -> bool:
    """True when the PNG already carries an alpha channel with transparent
    pixels, i.e. it has been matted. Unreadable files are reported as matted so
    repair leaves them for build_masks to skip and report."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.load()
        return im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 0
    except Exception as e:  # noqa: BLE001 - any decode failure
        print(f"saezuri-worker: cannot read {path.name} ({e}); leaving it alone",
              file=sys.stderr)
        return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--generate", nargs="*", default=None, metavar="SCI|COM",
                    help='Generate art for these "scientificName|commonName" species, '
                         "then rebuild the manifest.")
    ap.add_argument("--rebuild", action="store_true",
                    help="Ensure the fallback + rebuild the manifest and exit (no generation).")
    ap.add_argument("--repair", action="store_true",
                    help="Matte any cutout left fully opaque by an older pipeline version, "
                         "rebuild the manifest, and exit (no generation).")
    ap.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS_DIR,
                    help="Served illustration directory (default: the container path)")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                    help="Writable cache for fetched Wikipedia reference photos")
    ap.add_argument("--poses", nargs="+", type=int, default=[1, 2], choices=[1, 2],
                    help="Which poses to generate. 1=perched, 2=flight. Default: both.")
    ap.add_argument("--notes", type=Path, action="append", default=None, metavar="PATH",
                    help="Operator prompt-addenda file, layered over the bundled "
                         "species-notes.json. Repeatable; later files win per key.")
    ap.add_argument("--force", action="store_true",
                    help="Re-render even if the pose already exists (use after editing a note).")
    ap.add_argument("--max-per-cycle", type=int, default=0,
                    help="Cap species generated this run (0 = no cap).")
    args = ap.parse_args()

    assets_dir: Path = args.assets_dir
    refs_dir: Path = args.cache_dir / "references"
    assets_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    # A valid manifest must always exist so the frontend fetches a real file
    # (built from whatever art is present - initially just the fallback).
    ensure_fallback(assets_dir)

    if args.repair:
        repair(assets_dir)
        rebuild_manifest(assets_dir)
        print("saezuri-worker: manifest rebuilt")
        return 0

    species = parse_species_args(args.generate or [])
    if not species:
        # --rebuild, or --generate with no species: just refresh the manifest.
        rebuild_manifest(assets_dir)
        print("saezuri-worker: manifest rebuilt")
        return 0

    # Resolved here only to fail before any work is done; pregen reads the same
    # environment and settles it again for the call itself.
    try:
        imageapi.resolve(None, None, None, imageapi.DEFAULT_IMAGE_MODEL)
    except imageapi.ConfigError as e:
        print(f"saezuri-worker: cannot generate — {e}", file=sys.stderr)
        return 2

    if args.max_per_cycle > 0 and len(species) > args.max_per_cycle:
        species = species[:args.max_per_cycle]

    seed_anti_refs(refs_dir)
    # Bundled notes first, the operator's own layered over them, so a local
    # tweak wins without having to restate what the pipeline already knows.
    notes_paths = [BUNDLED_NOTES, *(args.notes or [])]
    missing = [(sci, com, slugify(sci)) for sci, com in species]
    generate(missing, assets_dir, refs_dir,
             poses=args.poses, notes_paths=notes_paths, force=args.force)
    # Count the poses that actually landed, not the species: the service asks for
    # one pose at a time, so a species count would read 0/1 for a flight render.
    wanted = [(slug, POSE_SUFFIXES[pose - 1]) for _, _, slug in missing for pose in args.poses]
    landed = sum(1 for slug, suf in wanted if (assets_dir / f"{slug}{suf}.png").exists())
    rebuild_manifest(assets_dir)
    print(f"saezuri-worker: generated {landed}/{len(wanted)} pose(s); manifest rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
