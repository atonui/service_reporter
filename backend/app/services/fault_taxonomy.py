from __future__ import annotations

import re
from dataclasses import dataclass


FAULT_CATEGORIES = (
    "MLC",
    "Beam generation",
    "Imaging",
    "Patient support",
    "Cooling",
    "Electrical power",
    "Software and controls",
    "Vacuum",
    "Safety system",
    "Mechanical",
    "Dosimetry and beam quality",
    "Other",
)

FAULT_SUBCATEGORIES = (
    "MLC interlock",
    "MLC leaf or motor",
    "Modulator / thyratron",
    "Gun / filament",
    "RF driver / klystron",
    "Beam steering / tuning",
    "kV imaging / CBCT",
    "MV imaging",
    "Patient support / couch",
    "Chiller / water cooling",
    "Mains / UPS / power supply",
    "Software / workstation",
    "Network / communication",
    "Vacuum / HVOC",
    "Door / safety interlock",
    "Mechanical motion",
    "Dosimetry / calibration",
)


@dataclass(frozen=True)
class FaultMatch:
    category: str
    subcategory: str
    matched_text: str


_RULES = (
    ("MLC", "MLC interlock", r"\bMLC\b[^\n]{0,60}\binterlock"),
    ("MLC", "MLC leaf or motor", r"\b(?:multileaf|leaf\s+[AB]?\d+|t[- ]?nut|MLC motor)\b"),
    ("MLC", "MLC interlock", r"\bMLC\b"),
    ("Beam generation", "Modulator / thyratron", r"\b(?:thyratron|modulator|CB8)\b"),
    ("Vacuum", "Vacuum / HVOC", r"\b(?:HVOC|vacuum|ion pump)\b"),
    ("Beam generation", "Gun / filament", r"\b(?:electron gun|gun current|filament)\b"),
    ("Beam generation", "RF driver / klystron", r"\b(?:klystron|magnetron|RF driver|RF power)\b"),
    ("Dosimetry and beam quality", "Dosimetry / calibration", r"\b(?:dose|dosimetry|calibrat|output constancy|ion chamber)\b"),
    ("Beam generation", "Beam steering / tuning", r"\b(?:beam steering|retun|BGM|beam symmetry|beam quality)\b"),
    ("Imaging", "kV imaging / CBCT", r"\b(?:CBCT|OBI|kV imag|kV source|kV detector)\b"),
    ("Imaging", "MV imaging", r"\b(?:MV imag|EPID|portal imag)\b"),
    ("Patient support", "Patient support / couch", r"\b(?:couch|patient support|table top|Exact couch)\b"),
    ("Cooling", "Chiller / water cooling", r"\b(?:chiller|cooling|water flow|water temperature|overtemp)\b"),
    ("Electrical power", "Mains / UPS / power supply", r"\b(?:UPS|mains|power supply|PSU|breaker|phase loss)\b"),
    ("Software and controls", "Network / communication", r"\b(?:network|communication|ethernet|DICOM|connection)\b"),
    ("Software and controls", "Software / workstation", r"\b(?:software|application|workstation|console|reboot|database)\b"),
    ("Safety system", "Door / safety interlock", r"\b(?:door interlock|emergency stop|E[- ]?stop|safety interlock)\b"),
    ("Mechanical", "Mechanical motion", r"\b(?:gantry|collimator rotation|bearing|motor|encoder|mechanical)\b"),
)


def classify_fault(values: list[str | None]) -> FaultMatch | None:
    text = "\n".join(value.strip() for value in values if value and value.strip())
    for category, subcategory, pattern in _RULES:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return FaultMatch(category, subcategory, match.group(0))
    return None
