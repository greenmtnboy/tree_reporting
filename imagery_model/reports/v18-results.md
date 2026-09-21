# v18 results

v18 completed; best checkpoint `010.ckpt`. Verified archive downloaded and
teardown acknowledged. Lambda reports no active instances. All four inference
coverage receipts are complete: SF train 2,871, Boston train 3,330, SF validation
567, Boston validation 636. Test remains sealed.

Goal-aligned F2 at confidence 0.35:

| Matching | SF v16 -> v18 | Boston v16 -> v18 |
|---|---:|---:|
| Crown radius / 2, clipped 2-4 m | 32.67% -> 34.94% | 20.26% -> 22.18% |
| Fixed 2 m | 32.34% -> 34.64% | 19.92% -> 21.77% |
| Fixed 4 m | 46.68% -> 49.35% | 28.05% -> 30.78% |

Both models use the inherited crown-center training policy. v18 used newer
published curation and further optimization from v16. Exported validation
ground-truth tables are exactly equal in both cities despite the newer review
snapshot (12,659 SF / 12,864 Boston); inventory-relative paired analysis also
measures zero validation-label effect. Goal scores use each run's frozen loss
masks; full mask equality has not been independently verified. Improvements
cannot be attributed solely to curation rather than additional optimization.

Raw goal metrics and input hashes: `artifacts/benchmarks/v18-v16-crown-center/`.
The generated REPORT.md there inherits an experiment-specific footer from the
v16 scoring script; use this writeup for v18 interpretation, not that footer.
