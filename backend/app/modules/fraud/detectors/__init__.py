"""Fraud detectors: plug-ins behind one interface (docs/M5-DESIGN.md D102, ``base.Detector``).

``DETECTORS`` is the only list of detectors. The per-deed handler, the nightly sweep and the CLI
all read it at call time, so adding a detector is: a new file here, one entry below and its
``detectors_enabled`` config key (see ``base.py``).
"""

from app.modules.fraud.detectors.base import Detector, Finding, SweepResult, SweepWindow
from app.modules.fraud.detectors.reciprocal import ReciprocalDetector
from app.modules.fraud.detectors.ring import RingDetector

__all__ = [
    "DETECTORS",
    "Detector",
    "Finding",
    "SweepResult",
    "SweepWindow",
    "detector_by_name",
]

DETECTORS: list[Detector] = [
    ReciprocalDetector(),
    RingDetector(),
]


def detector_by_name(name: str) -> Detector | None:
    return next((d for d in DETECTORS if d.name == name), None)
