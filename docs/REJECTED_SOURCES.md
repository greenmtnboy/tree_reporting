# Rejected and parked sources

Tree inventories and landmark registers we looked at and did not wire, with
the reason, so a later batch does not re-probe them or wire one by mistake.
Add a row whenever a lead is dropped. Licence rows matter most: a source here
for its licence stays out until the publisher's terms change or the publisher
gives written permission, and the row should say which.

A source with **no licence stated** is not a reason to skip it: wire it and
label it display-only (EXTENDING.md, step 10-11). Only an explicit
prohibition (no redistribution, written consent required, all reuse
forbidden) belongs under "Licence".

## Licence

| Source | Code (if scaffolded) | Checked | Why it is out | What would change it |
|---|---|---|---|---|
| Hutt City Council (Lower Hutt, NZ) street trees, ArcGIS item `237eb7c547e44b4eac64a64a02474daf` | NZLHT | 2026-11-21 | The council's open-data hub terms forbid republishing without the council's written consent. The ingest was written and then removed (batch 13, PR #266). | Written consent from Hutt City Council, or a CC BY release. |
| Sheffield City Council street trees, ArcGIS item `7b2df397e9994ca5984bad0b679ce6d9` | GBSHF | 2026-11-19 | The layer is under the OS INSPIRE End User Licence, which forbids redistribution. data.gov.uk lists OGL, but the council's own item is the stricter one. | Written permission from the council and Ordnance Survey. |
| City of Peterborough (ON) tree inventory, ArcGIS item `b5c84f53b4414d539bca94ba3383292c` | CAPBO | 2026-11-22 | The City's Open Data Terms of Use (data-ptbo.opendata.arcgis.com/pages/terms-of-use) license only "the unmodified Open Datasets", and redistribution requires giving each recipient the terms and obtaining "the recipient's agreement to abide by them"; all other rights reserved. A public map changes the data and cannot collect visitors' agreement. The ingest was written and removed (batch 14); its live MapServer `citymaps.peterborough.ca/.../TreesExternal/MapServer/1` (33k rows with DBH) states no licence but is outside the open-data catalogue. | An open licence (OGL-style) from the City, or written permission. |
| Victorian Heritage Database API (api.heritagecouncil.vic.gov.au), landmarks | | 2026-11-21 | Terms forbid reproduction without written permission. Victorian cities use the Vicmap VHR layer (CC BY 4.0) instead. | Not needed while Vicmap VHR is available. |

## Not an official or usable inventory

| Source | Checked | Why it is out |
|---|---|---|
| Zagreb, ArcGIS item `07e1bc958853430d84d1f0ba0a66fd7b` | 2026-11-17 | An Esri/GDi demo account (`gdi3demo`), not the city's; no licence. |
| Cape Town, ArcGIS item `4ea4a97a40744b90a99e87a66270b6be` | 2026-11-17 | Polyphagous shot-hole borer sightings, not a tree inventory. |
| Singapore, data.gov.sg `d_644ff187b6d14d6316f47284a4a6c81f` | 2026-11-17 | Heritage trees only. |
| Hong Kong, data.gov.hk `hk-housing-lt-lt` | 2026-11-17 | Housing Authority estates only. |
| Medellín, GeoMedellín dataset 398 | 2026-11-17 | The server returns 403 to scripted access. |
| Budapest, infogardenweb.hu/bpfatar | 2026-11-21 | The API needs authentication; no licence. |
| South Dublin County, ArcGIS item `af5db0cb62984fe8afea30e8b2389904` (IESDC) | 2026-11-17 | The council's March 2026 overwrite left 6,136 of 68,218 rows with a point. Recheck if the layer is repaired. |
| Fairfax City (USFXC), GMU student ArcGIS copy | 2026-11-08 | Unofficial copy, licence unclear; kept as calibration data only and never published. |
| Honolulu, ArcGIS item `2c573c2913434b6db62c75ceac32794b` | 2026-11-22 | A USDA Forest Service Region 5 copy (last edited 2022), not the city's layer. |
| Amherst MA, ArcGIS item `0d4e7c7e12ce42b5908570604ab8020d` | 2026-11-22 | An Amherst College student account's copy of the town's data. Wire the town's own hub layer instead if it is found. |
| Abbotsford BC, ArcGIS item `a6f44ffead21443c8b8201c0fef87e39` | 2026-11-22 | A consultant's account (`DHC_Consultant`), a 2019 snapshot last edited 2021. |
| Minneapolis, Ann Arbor, Lincoln NE leads | 2026-11-19 | University or personal accounts, not the city's. |

## Parked for territory

Usable sources that sit inside or across an existing city's territory and
need a carve (and a rebuild of the neighbour) first.

| Source | Neighbour |
|---|---|
| York Region ON | Toronto (CATOR) box |
| Albury NSW | Wodonga (AUWOD) box |
| St. Albert AB | Edmonton (CAEDM) box |
| Whitby ON | Ajax (CAAJX) box |
| Unley and Burnside SA | Adelaide (AUADL) box |
| Manningham and Brimbank VIC | Melbourne (AUMEL) box |
