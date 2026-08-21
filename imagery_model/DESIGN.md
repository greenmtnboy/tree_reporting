# Experiment design

## Claim being tested

Given only georeferenced raw overhead pixels, can one model recover municipal stem points and,
at those points, estimate DBH and taxonomy well enough to extrapolate beyond the municipal
inventory?

“Trunk” means the mapped stem point. A top-down sensor usually does not observe the physical
trunk. DBH is an inferred structural attribute and must be described that way in results.

## Differences from Miller et al. (2026)

| Component | Miller et al. | This scaffold |
|---|---|---|
| Prediction unit | supplied crown polygon | dense image pixel / stem center |
| Image signal | monthly PlanetScope spectra, 2018–2024 | raw multiband image chips initially |
| Structural input | 12 crown-level lidar metrics | none |
| Taxonomy | 18 genera | train-supported SF species plus genus auxiliary head |
| DBH | input filter / reference attribute | regression target |
| Split | random 80/20 tree rows | projected spatial blocks with boundary guard |
| Missing inventory trees | outside crown-classification task | positive-unlabeled detection mask |

Their result is an important upper-context benchmark, not a like-for-like baseline. Their strict
test accuracy was about 82%; their expanded test fell to about 72%, and their own crown audit
found one-to-one matches for only 61% of sampled polygons before conditional adjustments. This
experiment should retain the same distinction between a clean cohort and actual deployment
conditions.

## Leakage controls

1. Project points into the city's local metric CRS.
2. Hash 768 m blocks into 70/15/15 train/validation/test partitions.
3. Drop labels within 64 m of an edge whose neighboring block belongs to another split. A full
   chip can therefore not share pixels across partitions.
4. Derive the species vocabulary and channel statistics from training blocks only.
5. Use validation blocks for early stopping and thresholds. Open the test blocks once, after the
   pipeline is frozen.
6. Record imagery item IDs, acquisition dates, source inventory version, config, git revision,
   and random seed with every run.

The deterministic hash makes reruns comparable. It is not a substitute for a second-city test;
the eventual strongest claim would hold out an entire compatible city.

## Label policy

- Keep DBH in 4–60 inches for the initial clean cohort. The lower bound follows the mixed-pixel
  concern in the paper; the upper bound removes obvious SF source errors such as DBH=9999.
- Keep all otherwise eligible stems as center/DBH labels.
- Train species only for classes with at least 500 examples in training blocks. Rare taxa get
  `species_id=-1` and do not contribute species loss; they remain useful detection labels.
- Build genus classes from the selected training species and use genus as auxiliary supervision.
- Preserve plant date for later temporal QA. A tree planted after image acquisition cannot be a
  valid visual label.
- Never convert unlabeled vegetation into background. Low NDVI is the conservative default
  negative set; known points add a small locally supervised disk.

## Experiment gates

### Gate 0: data audit

- Verify acquisition date, license, CRS, band order, ground sampling distance, and nodata.
- Plot at least 200 random inventory points over imagery.
- Measure georegistration offsets by neighborhood and reject/repair systematic shifts.
- Quantify labels planted after acquisition and DBH/source outliers.

### Gate 1: attributes at known points

Before judging detection, sample fixed crops centered on held-out inventory points. Compare:

- majority-class genus/species baselines;
- DBH median-by-species baseline; and
- the raw-image encoder's DBH and taxonomy heads.

If the model cannot beat these at known points, dense detection will not rescue the idea.

### Gate 2: point detection

Train the dense heatmap with positive-unlabeled masks. Inspect whether errors are label omission,
georegistration, crown overlap, shadow, or model error. A small manually annotated set containing
both public and private trees is needed before claiming citywide recall.

### Gate 3: temporal satellite context

If Gate 2 works, add a co-registered temporal encoder for PlanetScope/SuperDove spring/fall
phenology and fuse it with the sub-meter spatial branch. Run ablations:

1. RGB only;
2. RGB+NIR;
3. sub-meter imagery plus PlanetScope time series; and
4. optional lidar, as an explicitly separate upper-bound experiment.

This preserves the raw-image premise while testing the paper's most transferable finding: spring
and fall spectral features carried much of the taxonomy signal.

## Known limitations

- Municipal coordinates and DBH may be stale relative to the image date.
- A street-tree inventory is not a random sample of all city trees; private-land taxa and size
  distributions can differ.
- Apparent DBH skill may partly learn species, neighborhood, crown-management, and age proxies.
- Positive-unlabeled training reduces false negatives in the loss but cannot measure recall on
  trees that were never labeled.
- Sub-meter aerial results do not by themselves establish a satellite-only result.
- Spatial blocking reduces local leakage, but imagery from the same acquisition and sensor still
  appears in every split.
