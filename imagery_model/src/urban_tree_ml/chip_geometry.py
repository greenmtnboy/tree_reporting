"""Chip ID coordinates: regular IDs are grid indices; shifted IDs are pixels."""
import re


CHIP_ID = re.compile(r'(?P<shift>shift_)?r(?P<row>\d{6,})_c(?P<column>\d{6,})')


def chip_offsets(chip_id, chip_pixels, *, id_chip_pixels=None):
    """Return row/column offsets in the destination raster's pixel grid.

    id_chip_pixels is the generating run's chip size. Only use conversion after
    verifying that source and destination are resolution-only derivatives with
    the same chip footprints; an ID alone does not establish imagery identity.
    """
    match = CHIP_ID.fullmatch(chip_id)
    if match is None:
        raise ValueError(f'Invalid chip ID {chip_id!r}')
    if chip_pixels <= 0 or (id_chip_pixels is not None and id_chip_pixels <= 0):
        raise ValueError('Chip sizes must be positive')
    scale = chip_pixels / (id_chip_pixels or chip_pixels) if match['shift'] else chip_pixels
    offsets = tuple(int(match[key]) * scale for key in ('row', 'column'))
    if any(not float(value).is_integer() for value in offsets):
        raise ValueError('Chip does not align to the destination pixel grid')
    return tuple(int(value) for value in offsets)


def corresponding_chip_id(chip_id, source_pixels, destination_pixels):
    if not chip_id.startswith('shift_'):
        chip_offsets(chip_id, destination_pixels)
        return chip_id
    row, col = chip_offsets(chip_id, destination_pixels, id_chip_pixels=source_pixels)
    return f'shift_r{row:06d}_c{col:06d}'
