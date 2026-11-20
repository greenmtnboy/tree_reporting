"""Explicit human taxonomy overrides, independent of position/review status."""
import re


def normalize_species_override(value):
    if not isinstance(value, str) or len(value) > 180 or any(ord(c) < 32 for c in value):
        raise ValueError('Use a scientific species name, a genus, or blank for unknown')
    value = ' '.join(value.split())
    if value.lower() in {'unknown', 'unknown species', 'unknown genus'}:
        return ''
    if value and (not re.fullmatch(r'[A-Za-z][A-Za-z .×-]*', value) or value.startswith('__')):
        raise ValueError('Use a scientific species name or genus, not a category')
    return value


def validate_corrections(records):
    if not isinstance(records, list):
        raise ValueError('Taxonomy corrections must be a list')
    keys = set()
    for row in records:
        if not isinstance(row, dict) or not {'tree_id', 'split', 'species'}.issubset(row):
            raise ValueError('Invalid taxonomy correction')
        if row['split'] not in {'train', 'validation'}:
            raise ValueError('Taxonomy corrections may only target development splits')
        normalize_species_override(row['species'])
        key = (str(row['tree_id']), row['split'])
        if key in keys:
            raise ValueError('Duplicate taxonomy correction')
        keys.add(key)


def apply_taxonomy_corrections(frame, records, taxonomy):
    validate_corrections(records)
    if not records:
        return frame
    result = frame.copy()
    species = {name: i for i, name in enumerate(taxonomy['species'])}
    genera = {name: i for i, name in enumerate(taxonomy['genera'])}
    for row in records:
        mask = result.tree_id.astype(str).eq(str(row['tree_id'])) & result.split.eq(row['split'])
        name = normalize_species_override(row['species'])
        genus = name.split()[0] if name else ''
        result.loc[mask, 'species'] = name
        result.loc[mask, 'genus'] = genus
        for field, vocab, value in [('species', species, name), ('genus', genera, genus)]:
            result.loc[mask, field + '_id'] = vocab.get(value, -1)
            result.loc[mask, field + '_eligible'] = value in vocab
    return result
