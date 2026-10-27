"""Interpreter: a canonical spec + bars -> Signals (per-signal entry, stop, target and geometry)."""

from engine.interpreter.signals import Signals, interpret

__all__ = ["Signals", "interpret"]
