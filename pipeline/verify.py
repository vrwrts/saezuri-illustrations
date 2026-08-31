#!/usr/bin/env python3
# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Ported from AvianVisitors (Teddy Warner) — CC-BY-NC-SA-4.0; see pipeline/LICENSE.
"""AvianVisitors - adversarial species-ID + anatomy check on illustrations.

An independent quality gate for the generated library. Each illustration
goes through a fresh vision-model call that is NOT told the target
species: it's asked to identify the bird, count wings/legs/heads/tails,
and flag any twig, perch, or anatomical anomaly. The guess is then
compared to the intended species. This catches drift that passes a quick
visual review - a stylized bird that reads as the wrong species, an extra
wing, a stray perch the prompt said not to draw.

Results are appended to verify-results.csv (slug, pose, target, guess,
match, confidence, anatomy counts, flags).

Usage:
    export GENERATE_API_KEY='your-key'
    python3 verify.py --labels labels.txt                 # whole library
    python3 verify.py --labels labels.txt calypte-anna    # one slug
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

import imageapi

#: Vision, not image-out, so this check never uses GENERATE_MODEL: one variable
#: cannot name both the model that draws and the model that grades.
VERIFY_TIMEOUT_S = 120

VERIFY_PROMPT = """You are a rigorous ornithologist examining a stylized kachō-e woodblock-style bird illustration. The bird in the image is intended to be a {target_com} ({target_sci}).

Analyze the image and respond ONLY with a valid JSON object (no other text, no markdown fences) with these fields:

{{
  "guessed_species_sci": "<your best guess at the scientific name, Latin binomial, e.g. 'Calypte anna'>",
  "guessed_species_com": "<your best guess at the English common name>",
  "guess_confidence": "<low | medium | high>",
  "matches_target": <true if your guess matches {target_sci} or {target_com}, otherwise false>,
  "wing_count": <integer number of wings visible>,
  "leg_count": <integer number of legs/feet visible>,
  "head_count": <integer number of heads>,
  "tail_count": <integer number of tails>,
  "has_stick_or_perch": <true if any twig, stick, branch, perch, leaf, or substrate is visible in the image; false if the bird floats alone>,
  "diagnostic_features_present": "<comma-separated list of species-diagnostic field marks you can see, e.g. 'red cap, pink breast, streaked back, conical bill'>",
  "diagnostic_features_missing": "<features the species SHOULD have but you don't see, or empty string if all match>",
  "anatomy_issues": "<any anomalies (extra wings, missing feet, deformed beak), or empty string>",
  "style_assessment": "<one of: 'true kachō-e' | 'kachō-e-influenced watercolor' | 'field guide illustration' | 'photographic'>"
}}

Be honest. If the bird looks more like a different species, say so. If the anatomy has issues, say so. Empty strings for fields where there's nothing to report."""


def slugify(sci: str) -> str:
    """Match avian/frontend/apt.js slugify() exactly."""
    return re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")


def load_labels(path: Path) -> dict[str, tuple[str, str]]:
    """Parse a Sci|Com label file into {slug: (sci, com)}."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        sci, com = (s.strip() for s in line.split("|", 1))
        out[slugify(sci)] = (sci, com)
    return out


def extract_json(text: str) -> dict | None:
    """The prompt asks for bare JSON, but models wrap it in fences or prose
    often enough to be worth stripping both."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def verify_one(api_url: str, api_key: str, model: str,
               png: Path, sci: str, com: str) -> dict | None:
    parts = [
        imageapi.text_part(VERIFY_PROMPT.format(target_sci=sci, target_com=com)),
        imageapi.image_part(png.read_bytes(), "image/png"),
    ]
    resp = imageapi.chat(api_url, api_key, model, parts,
                         timeout=VERIFY_TIMEOUT_S, label=png.name)
    return extract_json(imageapi.first_text(resp))


CSV_HEADER = ("slug,pose,target_sci,guessed_sci,guessed_com,matches,confidence,"
              "wings,legs,head,tail,has_stick,diag_present,diag_missing,"
              "anatomy_issues,style\n")


def csv_row(slug: str, pose: int, sci: str, v: dict) -> str:
    def q(key):
        return '"' + str(v.get(key, "")).replace('"', "'") + '"'
    return ",".join([
        slug, str(pose), sci.replace(",", " "),
        str(v.get("guessed_species_sci", "")).replace(",", " "),
        str(v.get("guessed_species_com", "")).replace(",", " "),
        str(v.get("matches_target", False)), str(v.get("guess_confidence", "")),
        str(v.get("wing_count", "")), str(v.get("leg_count", "")),
        str(v.get("head_count", "")), str(v.get("tail_count", "")),
        str(v.get("has_stick_or_perch", "")),
        q("diagnostic_features_present"), q("diagnostic_features_missing"),
        q("anatomy_issues"), str(v.get("style_assessment", "")),
    ]) + "\n"


def main() -> int:
    here = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*", help="Slugs to verify. Default: all in --dir.")
    ap.add_argument("--labels", type=Path, required=True,
                    help="Sci|Com label file (same one passed to pregen.py)")
    ap.add_argument("--dir", type=Path, default=here / "assets" / "illustrations",
                    help="Illustration directory (default: avian/assets/illustrations/)")
    ap.add_argument("--out", type=Path, default=Path("verify-results.csv"),
                    help="CSV output path (default: ./verify-results.csv)")
    ap.add_argument("--api-key", help=f"API key (or {imageapi.ENV_API_KEY} env)")
    ap.add_argument("--api-url",
                    help=f"OpenAI-compatible base URL (or {imageapi.ENV_API_URL} env; "
                         f"default {imageapi.DEFAULT_API_URL})")
    ap.add_argument("--model", default=imageapi.DEFAULT_VISION_MODEL,
                    help="Vision model used to grade the art "
                         f"(default {imageapi.DEFAULT_VISION_MODEL})")
    args = ap.parse_args()

    try:
        api_url, api_key, model = imageapi.resolve(
            args.api_key, args.api_url, args.model, imageapi.DEFAULT_VISION_MODEL)
    except imageapi.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    labels = load_labels(args.labels)

    if args.slugs:
        pngs = [args.dir / f"{s}.png" for s in args.slugs]
    else:
        pngs = sorted(args.dir.glob("*.png"))
    if not args.out.exists():
        args.out.write_text(CSV_HEADER)

    print(f"verifying {len(pngs)} illustrations against {len(labels)} labels "
          f"({model} via {api_url})\n")
    mismatches = 0
    for png in pngs:
        if not png.exists():
            print(f"  [skip] missing {png.name}")
            continue
        name = png.stem
        pose, slug = (2, name[:-2]) if name.endswith("-2") else (1, name)
        if slug not in labels:
            print(f"  [skip] no label for {slug}")
            continue
        sci, com = labels[slug]
        try:
            v = verify_one(api_url, api_key, model, png, sci, com)
        except Exception as e:
            print(f"  [fail] {png.name}: {e}", file=sys.stderr)
            continue
        if not v:
            print(f"  [fail] {png.name}: could not parse response", file=sys.stderr)
            continue

        match = v.get("matches_target", False)
        tag = "[ok]   " if match else "[MISS] "
        print(f"  {tag}{png.name}: reads as {v.get('guessed_species_com','?')} "
              f"(conf={v.get('guess_confidence','?')})" + ("" if match else f", expected {com}"))
        if not match:
            mismatches += 1
        flags = []
        if v.get("wing_count", 2) != 2: flags.append(f"wings={v.get('wing_count')}")
        if v.get("leg_count", 2) and v.get("leg_count", 2) > 2: flags.append(f"legs={v.get('leg_count')}")
        if v.get("has_stick_or_perch"): flags.append("has perch/stick")
        if v.get("anatomy_issues"): flags.append(str(v["anatomy_issues"]))
        if v.get("diagnostic_features_missing"): flags.append(f"missing: {v['diagnostic_features_missing']}")
        if flags:
            print(f"         [warn] {'; '.join(flags)}")
        with args.out.open("a") as f:
            f.write(csv_row(slug, pose, sci, v))

    print(f"\ndone. {mismatches} mismatch(es). results -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
