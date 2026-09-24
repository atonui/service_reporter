from __future__ import annotations

import re

PRODUCT_CATALOG: dict[str, str] = {
    "H19": "TrueBeam Platform",
    "H29": "Clinac",
    "HAL": "Halcyon",
}


def normalize_pcsn(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return normalized or None


def pcsn_details(value: str | None) -> dict[str, str | None]:
    """Resolve a PCSN using known product-code prefixes; never guess an unknown boundary."""
    pcsn = normalize_pcsn(value)
    if not pcsn:
        return {"pcsn": None, "product_code": None, "serial_number": None, "model": None}
    prefix = next(
        (code for code in sorted(PRODUCT_CATALOG, key=len, reverse=True) if pcsn.startswith(code)),
        None,
    )
    if not prefix:
        return {"pcsn": pcsn, "product_code": None, "serial_number": None, "model": None}
    serial = pcsn[len(prefix) :]
    if not serial or not serial.isdigit():
        return {"pcsn": pcsn, "product_code": None, "serial_number": None, "model": None}
    return {
        "pcsn": pcsn,
        "product_code": prefix,
        "serial_number": serial,
        "model": PRODUCT_CATALOG[prefix],
    }
