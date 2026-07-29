# saezuri-illustrations

Community-contributed **kachō-e bird cutouts** for
[Saezuri](https://github.com/vrwrts/saezuri), plus the **pipeline** that generates them.
A Saezuri deployment pulls a bird's art the moment BirdNET-Go reports that species — for
free, no API key — so collages fill in with real illustrations instead of silhouettes.

> **Non-commercial.** Everything here (art *and* pipeline) is CC-BY-NC-SA-4.0, inherited from
> the AvianVisitors → BirdNET-Pi lineage. See [`LICENSE`](LICENSE) and [`ATTRIBUTION.md`](ATTRIBUTION.md).

## Layout

```
illustrations/<slug>.png (+ <slug>-2.png)   flat folder of cutouts, one PR at a time
pipeline/                the generation tooling (pregen → matte → build_masks, …)
scripts/validate.sh      lint illustration PNGs (also run in CI)
```

- **`illustrations/`** is a single flat folder — no per-region subfolders (a bird occurs in many
  regions; folders would duplicate it). `<slug>` is the scientific name slugified
  (`Turdus merula` → `turdus-merula`); `-2` is the flight pose. Authorship is the PR committer in
  git history — there are no per-image metadata files.
- **`pipeline/`** is the canonical home of the generator. Contributors run it to make art; the
  Saezuri app vendors it into its image at a **pinned version** (a `pipeline.tar.gz` GitHub Release
  asset, cut by [`release.yml`](.github/workflows/release.yml) on `pipeline/**` changes).

## How the app consumes this

- **Art:** per detected species, the app fetches
  `https://cdn.jsdelivr.net/gh/vrwrts/saezuri-illustrations@main/illustrations/<slug>.png` (jsDelivr
  CDN over this public repo). No publish step here — adding art is *just committing PNGs*.
- **Pipeline:** the app pins `PIPELINE_VERSION=vX.Y.Z` and downloads that release's `pipeline.tar.gz`
  at build time, so the generator and the art it produced never drift.

## Contributing

Generate a region's birds with your own keys, commit the PNGs, open a PR — see
[CONTRIBUTING.md](CONTRIBUTING.md). CI lints the changed images; a maintainer reviews the art
itself.
