"""Backtesting engine: data, blocks, rule spec, interpreter, simulator. No Django."""

# Bump when a change to blocks, the interpreter or the simulator changes results for an unchanged
# spec. It is part of the spec hash, so stored trials are never mixed with results of a newer engine.
ENGINE_VERSION = 1
