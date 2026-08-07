"""
Proxy shim for `pkg_resources` to improve compatibility on environments
where `pkg_resources` isn't installed as a top-level package but exists
inside pip or setuptools vendors. This allows `import pkg_resources` to
succeed by delegating to `setuptools._vendor.pkg_resources` or
`pip._vendor.pkg_resources`.
"""
import importlib

_candidates = [
    "setuptools._vendor.pkg_resources",
    "pip._vendor.pkg_resources",
]

_pkg = None
for name in _candidates:
    try:
        _pkg = importlib.import_module(name)
        break
    except Exception:
        _pkg = None

if _pkg is None:
    raise ImportError("pkg_resources shim could not find a provider in setuptools or pip vendors")

# Re-export public attributes
for k, v in vars(_pkg).items():
    if not k.startswith("_"):
        globals()[k] = v

__all__ = getattr(_pkg, "__all__", [k for k in globals().keys() if not k.startswith("_")])
