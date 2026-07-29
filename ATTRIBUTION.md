# Attribution

This repository holds both AI-generated kachō-e (bird-and-flower woodblock) **illustrations**
and the **pipeline** (`pipeline/`, ported from AvianVisitors) that generates them. Both are
distributed under **CC-BY-NC-SA-4.0** — the license inherited through the AvianVisitors →
BirdNET-Pi lineage. Treating the generated art as adapted material of this lineage is the
deliberate, conservative stance; art and pipeline are therefore non-commercial and share alike.

Credit, in lineage order:

- **AvianVisitors** — Teddy Warner. <https://github.com/Twarner491/AvianVisitors>
  Design, the kachō-e collage aesthetic, and the illustration pipeline this art is
  generated with.
- **BirdNET-Pi** — Patrick McGuire. The CC-BY-NC-SA-4.0 license and style lineage that
  AvianVisitors builds on.
- **BirdNET-Lite** — K. Lisa Yang Center for Conservation Bioacoustics, Cornell Lab of
  Ornithology, Cornell University.

Changes made relative to the source: the art is restyled kachō-e, generated per-region
(European and other species sets), rendered on a flat magenta chroma-key ground and cut
out with Saezuri's numpy/scipy region matte, then reduced to silhouette masks for the
collage packer.

## What this obliges (when using or redistributing the art or pipeline)

- **BY** — keep this attribution and note that changes were made.
- **NC** — non-commercial use only: no ad-supported hosting, paid tiers, sold prints, or
  bundling into a commercial product.
- **SA** — redistribute under CC-BY-NC-SA-4.0, with no added restrictions or DRM.

The `pipeline/` directory carries its own `LICENSE` so it stays self-describing when the Saezuri
app vendors it into its image. `pipeline/assets/` reference art has its own provenance file
(`pipeline/assets/ATTRIBUTION.md`).
