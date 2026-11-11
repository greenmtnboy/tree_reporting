# Species enrichment

One city-agnostic table, `tree_enrichment_v{n}.parquet`, keyed on the
scientific name, maps each species to common names, growth form, ecology
(`native_ecoregions`, `is_evergreen`, height and spread ranges, `bloom_months`
as month numbers, tolerances, USDA zones) and two licensed photos
(`photo_url`, the leaf close-up fetched by the run; `trunk_photo_url`,
the full-tree view picked by hand in the admin form). The full-tree photo
should show the whole tree, including its crown and overall shape. The legacy
`trunk_photo_*` field names are retained for compatibility; the editor and
website label this view **Full tree**, and the close-up **Leaf**. Existing
photos remain until reviewed; changing the label does not replace their images.
The browser joins `t.species = se.species`
and takes the first common name as the label. The model is
`data/raw/tree_enrichment.preql`; the job is `data/raw/enrichment/`.

## The key rule

Every tree parquet's `species` is the accepted binomial and nothing else.
`sanitize_species`, called for every city inside `enforce_tree_schema`:

- strips accents, then decides whether the value is a taxon at all;
- truncates to species rank (a variety, subspecies or quoted cultivar is
  removed; the cultivar goes to the `cultivar` column);
- emits one hybrid spelling, ASCII `x`; a leading mark keeps its capital;
- folds `SPECIES_SYNONYMS` (a name Kew's POWO lists under an accepted one)
  and `SPECIES_MISSPELLINGS` (a name that does not exist) onto the accepted
  name; both live in `shared/ingest.py`, keys and values in the sanitised
  form, a value never itself a key (`test_ingest_shared.py`);
- turns what is left that is not a taxon into a **sentinel**, never null:
  `Unknown` for most, `Palm`, `Shrub`, `Cactus` and `Dead` where the source
  recorded a growth form or a dead standing tree (`SPECIES_SENTINELS`, with
  multilingual spellings in `_FORM_SENTINEL_ALIASES`; each needs an entry in
  `src/src/data/species.ts`);
- drops a row that describes an empty site (`Vacant`, `Stump`, a planting
  site) via `is_not_a_tree`;
- rewrites values that name no taxon but carry a genus (`_NON_TAXON_REWRITES`,
  a list built only from observed inventory values) and prints a summary of
  everything it reshaped.

The enrichment queue asks `is_enrichable_species`, which defers to
`sanitize_species`: a species is enrichable when the ingest would keep it
exactly as written, so tightening the ingest shrinks the queue in the same
edit.

## Synonyms and misspellings

Run `tools/species_audit.py` (`--map` prints entries to paste). It finds
every pair of published names within two edits and adjudicates each one
against Kew's World Checklist of Vascular Plants (WCVP, the names POWO
serves), read from `wcvp_names_v{n}.parquet`, which the weekly `refresh-wcvp`
job publishes from Kew's bulk download (`raw/wcvp_info.preql`,
`shared/wcvp.py`). POWO's API has refused scripted requests since late 2026,
and Kew asks bulk consumers to use WCVP. `--wcvp` reads a local `wcvp.zip`
instead. The audit refuses a pair when both names are accepted, when a name
is a homonym (Kew has more than one record), when a misspelling has two
candidates, or when the edit distance is large for a short word. Tree counts
are printed but are not evidence. A name merely misapplied in the trade is
not a synonym and does not go in the map. When Kew and the published table
disagree on which of two names is accepted, the published table wins, so one
taxon stays one row.

On every load the job (`with_species_aliases` in `_tree_shared.py`) folds a
row keyed by a synonym or misspelling onto the accepted row, lists synonyms
(not misspellings) in the accepted row's `synonyms`, and publishes an alias
row under every synonym, misspelling and hybrid-mark twin so a tree row still
carrying the old name keeps its label. The admin form shows an alias row
read-only. Adding a duplicate's name to the accepted row's `synonyms` in the
form bridges the join immediately; the ingest map is what changes what
cities publish.

## What the table must not contain

- **Sentinels and chimeras are never enriched.** A row keyed `Unknown`
  would label every unidentified tree in every city. Exclusion
  (`SKIP_SPECIES`, `SPECIES_EXCLUSION_SQL`) derives from `SPECIES_SENTINELS`;
  `purge_non_taxa` removes any such row already present on every load.
  `CHIMERA_SPECIES` lists real-genus-plus-wrong-epithet names that have
  nothing to find; they are excluded from the queue but their rows are kept.
- **Keys nothing can join to are purged** (`purge_unreachable_keys`), after
  aliasing, never before; a key the published data still carries is kept
  whatever the ingest would now do with it, and an unreachable rollup means
  keep everything.
- **Common names are sentence case** (`normalize_common_names` in
  `_common_name_style.py`), with proper nouns restored from two curated
  lists; a name that comes out wrong is fixed by one list entry. It runs on
  load, on each LLM row, and on each admin save.

## Leaf and bark shades

Three CSS hex columns, `#rrggbb` lowercase: `foliage_colors` (the
growing-season leaf, most common shade first; usually one green, a second for a
purple-leaved or blue-green species), `fall_foliage_colors` (the autumn leaf,
empty for an evergreen) and `trunk_color` (the mature bark). The map's
**Leaf shade (seasonal)** colour mode (`?colorBy=foliage`,
`src/src/lib/foliageSeason.ts`) paints each tree with the shade for the season,
decided from the calendar and the city's hemisphere: autumn is
September to November north of the tropics and March to May south of them;
December to March a deciduous tree is shown by its bark, or a bare-branch
grey when none is recorded; inside the tropics it is always the growing season.
A list with two shades is spread across the species' trees by tree id. A
species with no shade keeps its tree-form colour, which is most of the map
until coverage grows. Coverage is sparse on purpose; the fields are not part
of `ENRICHMENT_COMPLETE_SQL`.

Where a shade comes from, most trusted first (`enrichment/_foliage_colors.py`):

1. the admin form, which accepts `#abc` and a missing `#` and refuses
   anything that is not a colour;
2. the LLM run, which is asked for all three alongside the other fields and
   told to estimate leaf colour from the genus rather than leave it empty
   (`normalize_hex_colors` drops anything that is not hex);
3. `HERO_FOLIAGE`, hand-authored shades for about twenty-five of the most
   planted street trees, applied on every load wherever a row still has none
   (`with_hero_foliage`). A floor, never an override: a value the model or a
   reviewer wrote is kept;
4. the leaf photo: `backfill.py --fields foliage_colors` reads each species'
   iNaturalist leaf close-up and keeps the dominant greens
   (`leaf_colors_from_pixels`: pixels gated to leaf hues, binned, the biggest
   bins' means; a second shade only when it holds 15% of the leaf and sits
   apart from the first). It fills `foliage_colors` only. A purple-leaved
   cultivar fails the green gate on purpose, and a full-tree photo is too
   much sky and lawn to read bark from. With `--model` the LLM answers
   instead.

The sentinels have no shades. The website catalogue does not carry them yet.

## How the job behaves

- `refresh-enrichment` ticks daily and **runs `main`**, so a change to the
  table's shape is reverted on the next tick until it merges.
- The probe and the run share one definition of complete
  (`ENRICHMENT_COMPLETE_SQL`: a common name and a growth form). Incomplete
  rows older than `REENRICH_INCOMPLETE_BEFORE` are re-asked once; move that
  date to ask again. `merge_with_existing` replaces an existing row rather
  than refusing, because `species` is the grain.
- The prompt asks for `common_names` and `tree_form` explicitly and re-asks
  once when the name comes back empty; a species that returns empty twice is
  taken at its word. Model: `google/gemini-3.5-flash`, overridable with
  `TREE_ENRICHMENT_MODEL` (2.5 Flash is past its Vertex retirement date;
  3.1 Flash-Lite is faster but invented a cultivar name in the comparison).
- The model reads four sources per species (`_tree_enrichment_sources.py`):
  Wikipedia (the intro plus the Description, Distribution, Ecology and
  Cultivation sections; for a species with no English article, its article
  in Spanish, Portuguese, French, German and so on, found through Wikidata),
  Kew's WCVP, GBIF and SelecTree. WCVP is read from
  the parquets `refresh-wcvp` publishes, prefetched once for the whole queue
  (`WCVP_ZIP` reads a local zip); it gives the status, accepted name, any
  homonym or misapplication, lifeform, climate and every TDWG level-3 area,
  native or introduced, which is what the ecoregion shortlist is matched
  from. It has no prose descriptions; those come from Wikipedia. If the
  parquets can't be read, the run warns once and carries on without Kew.
- Credential: Vertex, project `preqldata`, location `global`. The cloud job
  runs as the trilogy-cloud tenant SA
  `t-0a01c13d3f3f-prod@trilogy-data-prod.iam.gserviceaccount.com` (metadata
  server, `cloud-platform` scope), which needs `roles/aiplatform.user` on
  `preqldata`; a workstation uses `gcloud auth application-default login`.
  With no credential at all the script aborts before sourcing anything.
- Five LLM failures in a row abort the run non-zero. The platform shows only a
  failed run's stderr, and per-species errors used to be swallowed: the job ran
  green from Oct 16 to Nov 7 2026, every call 403ing, and enriched nothing.
- `--time-budget` (default 2700 s, `TREE_ENRICHMENT_TIME_BUDGET_S`) stops
  starting species and publishes what is done. The table is emitted only at
  the end, so a run killed at the job's 3600 s timeout would publish nothing.
  At ~13 s a species that is about 200 per run.
- `assert_checkpoint_is_not_stale` refuses to resume from a local `--output`
  checkpoint holding fewer rows than the published table;
  `MAX_SPECIES_PER_RUN` caps a run and reports what it left;
  `assert_published_matches` reads the upload back and checks the row count
  and the sentinel set.

```bash
cd data/raw && uv run tree_enrichment_probe.py                      # true, or the missing species
cd data/raw && uv run tree_enrichment.py --limit 50 --output tree_enrichment.parquet
```

## Family

`family` is Kew's and never the model's (`family_from_checklist` in
`_tree_enrichment_sources.py`): the family on the name's own WCVP record, else
its genus's, when Kew has one accepted genus of that name (`Ilex` L. answers,
its sunk homonym in Fagaceae does not; two accepted genera, or none and a
disagreement, answer nothing). A run sets it on each row it writes, and every
load fills whatever is still null (`with_wcvp_family`, before the alias step),
so the column heals itself and a value typed in the admin form is kept.
Sentinels have none. About 250 keys stay null: the sentinels, orders
(`Pinales`), and genera Kew does not have because they are misspelt.

## Backfilling existing rows

`enrichment/backfill.py` fills null fields on rows the job will not revisit
(it re-asks only a row with no common name or growth form). `--fields family`
reads WCVP and needs no model. For the LLM traits, `--order trees
--tree-coverage 0.97` asks the species most trees are, most planted first;
`--asked-log` records each species asked so a resumed run does not ask again
for a null that came back null; `--only-requested` writes only the fields
named; `--time-budget` ends a batch with its checkpoint written. The backfill
keeps only ecoregion ids from the shortlist. Any upload goes through
`publish`: the published table is re-read and is the base, the checkpoint
fills only what is still empty there, and the upload is refused if a published
row or value would be lost (`--publish-checkpoint` does only this step).

## Correcting by hand

`enrichment/admin/server.py` is a localhost form over the table. Edits are
staged locally; Publish re-reads the live parquet, patches the staged rows and
every alias row of the taxon, uploads, and reads back to verify. An edited
row carries today's `enriched_at`, which keeps the daily job from overwriting
it; the form refuses a row without a common name and a growth form. Needs
`gcloud auth application-default login`; `tests/test_enrichment_admin.py`
pins the invariants.
