# Illustration pipeline

Backend-agnostic tooling for generating the kachō-e bird cutouts and the collage
silhouette masks. **Ported from [AvianVisitors](https://github.com/Twarner491/AvianVisitors)**
with attribution preserved — this is the one thing Saezuri ports rather than
reimplements (see CLAUDE.md). The art and this tooling are **CC-BY-NC-SA-4.0**
(non-commercial); confirm the obligations before publishing generated images.

## Files

| File                 | Origin                | What it does                                                        |
| -------------------- | --------------------- | ------------------------------------------------------------------- |
| `prompt.template.md` | AvianVisitors (**adapted**) | Image-model prompt template. Adapted: the ground is now flat **magenta** (a chroma backing `matte.py` removes), not cream. |
| `pregen.py`          | AvianVisitors (**adapted**) | Generate perched + flight illustrations for a species list. Adapted: `--matte` cuts each render before it is written, and `--notes` takes layered per-species prompt addenda. |
| `matte.py`           | Saezuri (original*)  | Remove the magenta ground by **region matte** (numpy + scipy, no ML model) and crop to the bird. (*bbox-crop ported from AvianVisitors `cutout.py`.) |
| `matte_test.py`      | Saezuri (original)   | Offline correctness tests for `matte.py` (no key/network needed).   |
| `pregen_test.py`     | Saezuri (original)   | Offline tests for notes layering, prompt assembly, and render writing (no key/network needed). |
| `imageapi.py`        | Saezuri (original)   | OpenAI-compatible `chat/completions` client — the only place the model provider is known. |
| `imageapi_test.py`   | Saezuri (original)   | Offline tests for `imageapi.py`, against a localhost stub server (no key/network needed). |
| `species-notes.json` | Saezuri (original)   | Bundled per-species prompt addenda; an operator's file layers over it. |
| `build_masks.py`     | AvianVisitors (**adapted**) | Build the silhouette masks. Adapted to emit a JSON manifest (below) instead of rewriting `apt.js`, and to add a fallback entry. |
| `verify.py`          | AvianVisitors (**adapted**) | Optional blind QA of each render via a vision model. Adapted to call through `imageapi.py`. |
| `make_fallback.py`   | Saezuri (original)   | Draw the generic fallback silhouette (`_fallback.png`).             |

## Pipeline order

`pregen.py --matte` → `build_masks.py` (`verify.py` optional). Generation needs
`GENERATE_API_KEY`; the `requirements.txt` deps are just Pillow + numpy + scipy (no ML
model and no vendor SDK), so the runtime image is `nginx:alpine`.

## Choosing a model

`imageapi.py` speaks OpenAI-compatible `chat/completions`, so the endpoint is config:
`GENERATE_API_URL` (default `https://openrouter.ai/api/v1`) and `GENERATE_MODEL`
(default `google/gemini-2.5-flash-image`). Any server with that shape works, a local one
included — `GENERATE_API_URL=http://localhost:1234/v1`.

`chat/completions` rather than a dedicated images endpoint because this prompt sends
**captioned** references (`IMAGE 1 (positive…)`, `IMAGE 2 (negative, do NOT copy)`), and
only a messages array keeps text and images interleaved in that order.

The hard requirement on a model is the magenta ground: the prompt asks for the bird on a
flat `#FF00FF` backing because no image model emits alpha, and `matte.py` removes it
afterwards. A model that ignores the instruction produces a magenta rectangle, not a
cutout — `validate.sh` warns on a fully-opaque image, which is the symptom.

`--matte` calls into `matte.py` in-process, so the cutout happens before the render is
ever written under its real filename. That ordering is deliberate: the output directory
is the one nginx serves and `build_masks.py` scans, so a magenta render sitting there
under a real name would be published, and a run killed between render and cutout would
strand it permanently. `matte.py` remains a standalone CLI for one-off use, and
`worker.py --repair` uses it to clean up files left behind by pipeline versions that
matted in a second pass.

## Species notes

When a species reliably comes out wrong, add a note rather than re-rolling the dice:

```bash
python3 pregen.py --species "Turdus merula|Blackbird" --notes my-notes.json --force
```

`species-notes.json` in this directory is the bundled layer that ships with every
pipeline release. `--notes` is repeatable and later files win per key, which is how an
operator's own file (on a writable volume) refines the bundled set without replacing it.
A key may be a scientific name or its slug. A note that works is worth a PR against
`species-notes.json` so every deployment gets it.

Editing a note does not by itself replace art that already exists — pass `--force`, or
let the refresh service notice the changed note and re-render.

## Cutout: region matte on a magenta ground

The image model can't emit a clean alpha channel, so `pregen.py` renders each bird on a
flat, saturated **magenta** ground and `matte.py` removes it. Magenta is chosen because
it sits far from every natural plumage colour, so pale birds aren't eaten.

Removal is treated as **segmentation, not colour**: `matte.region_matte` scores each
pixel's magenta-ness `m = min(R,B) − G` (a hue measure that is ~0 on red/warm plumage),
then takes the background to be the magenta region **connected to the frame border**,
plus any pocket as magenta as the ground itself (to catch gaps enclosed by the bird,
e.g. between an owl's talons). The bird's own colours are never altered — only a thin
edge band is feathered. This is what a global colour key can't do: tell ground-pink
from a red bill, or keep warm tones (models tend to paint a per-image, slightly
non-uniform rose rather than exactly `#FF00FF`, which `matte.py` auto-detects from a
border ring).

`matte.py` needs no rembg / onnxruntime / ~1 GB model — the reason the runtime image
dropped from a Debian + baked-model build to `nginx:alpine`. Correctness is covered
offline by `python3 -m unittest pipeline.matte_test`.

## Generating the layout manifest (step 3)

`build_masks.py` reads the cutout PNGs in `public/assets/illustrations/` and writes
`public/layout-manifest.json`:

```jsonc
{
  "dims":  { "<slug>": [w, h] },                    // aspect, long side 560
  "masks": { "<slug>": { "w":.., "h":.., "bits": "<base64>" } }, // 1-bit silhouette, long side <=93
  "fallbackKey": "_fallback"
}
```

Only Pillow is required for step 3 and the fallback:

```sh
python3 -m venv .venv && ./.venv/bin/pip install 'Pillow>=10.0'
./.venv/bin/python pipeline/make_fallback.py      # once: writes _fallback.png
./.venv/bin/python pipeline/build_masks.py        # writes public/layout-manifest.json
```

The app fetches the manifest at boot and, when it is absent, falls back to a built-in
manifest holding only the generic silhouette (`src/hooks/useLayoutManifest.ts`).

## v1 note

The bundled AvianVisitors cutouts are a western-U.S. set used as **dev placeholders**;
they are gitignored and never shipped. Regenerating a European species set via
`pregen`/`matte`/`build_masks` is a later task.
