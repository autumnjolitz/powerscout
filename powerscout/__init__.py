from .about import __version__
__version__  # Silence unused import warning.

try:
    import __pypy__
    __pypy__
except ImportError:
    try:
        import ujson as _json
    except ImportError:
        import json as _json
else:
    import json as _json
_json
