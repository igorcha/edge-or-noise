"""Canonical JSON and the spec hash.

The hash is a trial's identity: same hash, same trial; a different hash is a new trial
and counts against the strategy. It's taken over the canonical form (see checks.py) with
sorted keys and no whitespace, so key order, aliases and spelled-out defaults don't matter,
but any parameter, rule or slippage change does. The engine version is hashed in too: a change
to how blocks or fills work gives different results, so the same spec under it is a new trial.
"""

import hashlib
import json

from engine import ENGINE_VERSION
from engine.spec.checks import canonicalize
from engine.spec.models import Strategy


def canonical_dict(spec: Strategy) -> dict:
    return canonicalize(spec).model_dump(mode="json", by_alias=True)


def canonical_json(spec: Strategy) -> str:
    return json.dumps(canonical_dict(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def spec_hash(spec: Strategy) -> str:
    return hashlib.sha256(f"engine:{ENGINE_VERSION}\n{canonical_json(spec)}".encode()).hexdigest()
