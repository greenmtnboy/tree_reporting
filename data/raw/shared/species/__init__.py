"""Common name to accepted binomial, one module per language.

A portal that publishes a vernacular name where the binomial should be
needs a curated table, and the tables cannot be merged: their keys
normalise by different rules, and `common_name_key`'s reduction to
`[a-z ]` erases a katakana name entirely. A second language gets a second
module and a second key function, not a wider regex.

An automatic index (GBIF, the enrichment table's inverse) is a drafting
aid; each table was put to POWO value by value. See each module's
docstring for what its index got wrong.
"""
