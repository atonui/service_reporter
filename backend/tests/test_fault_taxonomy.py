from backend.app.services.fault_taxonomy import classify_fault


def test_classifies_mlc_interlock() -> None:
    match = classify_fault(["MLC Interlocks", "Replaced motor on leaf A29"])
    assert match is not None
    assert match.category == "MLC"
    assert match.subcategory == "MLC interlock"


def test_classifies_pel_thyratron_issue_as_beam_generation() -> None:
    match = classify_fault(
        [
            "Machine dropping into PEL",
            "CB8 in the modulator tripped",
            "Replaced main thyratron and cleared the issue",
        ]
    )
    assert match is not None
    assert match.category == "Beam generation"
    assert match.subcategory == "Modulator / thyratron"


def test_returns_none_without_supported_fault_terms() -> None:
    assert classify_fault(["Machine requires investigation"]) is None
    assert classify_fault(["Machine dropping into PEL"]) is None
