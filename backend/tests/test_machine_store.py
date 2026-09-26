from backend.app.schemas.machine_registry import MachineRegistrationInput
from backend.app.services.machine_store import (
    create_registered_machine,
    list_registered_machines,
    update_registered_machine,
)


def test_machine_registry_create_list_and_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "registry.db"))
    created = create_registered_machine(
        MachineRegistrationInput(
            customer_name="Coast General Hospital",
            pcsn="h196237",
            quarterly_hours="520",
        )
    )

    assert created.pcsn == "H196237"
    assert created.product_code == "H19"
    assert list_registered_machines() == [created]

    updated = update_registered_machine(
        created.id,
        MachineRegistrationInput(
            customer_name="Coast General Teaching and Referral Hospital",
            pcsn="H196237",
            active=False,
        ),
    )

    assert updated is not None
    assert updated.customer_name == "Coast General Teaching and Referral Hospital"
    assert updated.active is False


def test_machine_registry_rejects_duplicate_pcsn(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "registry.db"))
    value = MachineRegistrationInput(customer_name="Customer A", pcsn="HAL1124")
    create_registered_machine(value)

    try:
        create_registered_machine(
            MachineRegistrationInput(customer_name="Customer B", pcsn="hal1124")
        )
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate PCSN should be rejected")
