"""Required native review primitives. No automatic backend or value fallback.

TREE_ML_REVIEW_NATIVE=off explicitly selects Python for benchmarks/rollback.
The default required mode fails at import if the extension is unavailable.
"""
from copy import deepcopy
import json
import os


_MODE = os.environ.get('TREE_ML_REVIEW_NATIVE', 'required').lower()
if _MODE not in {'off', 'required'}:
    raise ValueError('TREE_ML_REVIEW_NATIVE must be required (default) or explicitly off')
_native = None
if _MODE != 'off':
    try:
        from urban_tree_ml import _review_native as _candidate
        if _candidate.API_VERSION != 2:
            raise ImportError('Review native API version mismatch; rebuild the extension')
        _native = _candidate
    except (ImportError, OSError, AttributeError) as error:
        raise ImportError('Native review acceleration is required; run native/build_review_native.py') from error


def _python_encode(value, pretty=False):
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def encode_json(value, pretty=False):
    if _native is not None:
        return _native.encode_json(value, pretty)
    return _python_encode(value, pretty)


def copy_json(value):
    if _native is not None:
        return _native.copy_json(value)
    return deepcopy(value)


def backend_info():
    return {'backend': 'rust' if _native is not None else 'python', 'mode': _MODE,
            'api_version': _native.API_VERSION if _native is not None else None,
            'automatic_fallback': False}
