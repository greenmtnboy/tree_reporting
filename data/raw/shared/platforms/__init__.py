"""One reader per open-data platform this repo reads more than twice.

`arcgis` is what most North American cities publish on; `ckan`, `socrata`
and `wfs` cover the rest. Each hides that platform's paging rules and
freshness watermark, and each carries at least one detail that is
correctness rather than convenience -- read the module docstring before
hand-rolling a loop against a portal one of these already covers.

OpenDataSoft is deliberately absent: Paris, Vancouver and Melbourne are
three hand-rolled copies, and the module gets written when the shape is
knowable, not at the first city.
"""
