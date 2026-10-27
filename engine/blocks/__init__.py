"""Building blocks. Importing this package registers every block in REGISTRY."""

from engine.blocks import levels, series, structure, timing  # noqa: F401  (registration side effects)
from engine.blocks.context import Bars
from engine.blocks.registry import REGISTRY, catalog, compute, resolve

__all__ = ["Bars", "REGISTRY", "catalog", "compute", "resolve"]
