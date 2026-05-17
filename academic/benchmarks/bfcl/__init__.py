"""BFCL benchmark package.

Use submodules for new code:

- `academic.benchmarks.bfcl.adapter` for the BFCL executor/adapter API.
- `academic.benchmarks.bfcl.related.experiment` for the Train50/Heldout50 experiment.
- `academic.benchmarks.bfcl.maintenance` for BFCL-specific skill maintenance adapters.
"""
from __future__ import annotations

from academic.benchmarks.bfcl import adapter as _adapter

globals().update(
    {
        name: value
        for name, value in _adapter.__dict__.items()
        if name not in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}
    }
)
