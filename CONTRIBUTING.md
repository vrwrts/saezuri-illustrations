# Contributing illustrations

Add birds by generating them **with your own API keys**, committing the PNGs to
`illustrations/`, and opening a PR. Authorship is your commit — there's no metadata to fill in.

## Prerequisites

- A **Google AI (Gemini) API key** — generation uses the paid image API against *your* key.
- An **eBird API key** — to filter a species list down to a region (optional but recommended).
- A **labels file** — a superset species list to filter (e.g. BirdNET-Pi's `labels.txt`, or any
  file of `Scientific name_Common name` / `Sci|Com` lines).
- `python3` + the pipeline deps: `pip install -r pipeline/requirements.txt`.

## Generate, then PR

```bash
export GEMINI_API_KEY=...   EBIRD_API_KEY=...        # your keys — you pay for generation
python3 pipeline/pregen.py --labels labels.txt --ebird-region GB --out illustrations   # step 1: render
for f in illustrations/*.png; do                                                        # step 2: cut out
  python3 pipeline/matte.py --region "$f" --out "$f"
done
python3 pipeline/build_masks.py --illustrations illustrations --out /tmp/manifest.json  # step 3: sanity check
scripts/validate.sh illustrations                                                       # lint before committing
git add illustrations && git commit && git push        # on your fork's branch, then open a PR
```

`--ebird-region` is a *generation-time* filter (`GB`, `US-CA`, `US-CA-085`) — it just narrows which
species you render; nothing about regions is stored. Re-running skips species already present.

## Rules

- **Flat folder, real slugs.** Files go directly in `illustrations/` as `<slug>.png` /
  `<slug>-2.png`, lowercase `[a-z0-9-]`. No subfolders.
- **No `_fallback.png` / no underscore-prefixed files.** The Saezuri app ships its own fallback
  silhouette; the app and the linter reject underscore-prefixed names.
- **Cutouts must be transparent** (the matte step removes the magenta ground). `validate.sh` warns on
  a fully-opaque image.
- **Licensing.** By contributing you agree your art is published under **CC-BY-NC-SA-4.0**
  (non-commercial) — see [`LICENSE`](LICENSE) / [`ATTRIBUTION.md`](ATTRIBUTION.md).

CI (`validate-pr.yml`) re-lints changed PNGs and runs the pipeline tests if you touched `pipeline/`.
It catches structural mistakes only — a maintainer reviews the images for species accuracy and style.
