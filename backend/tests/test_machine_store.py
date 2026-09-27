from backend.app.schemas.machine_registry import MachineRegistrationInput
from backend.app.schemas.customer_alias import CustomerAliasInput
from backend.app.services.customer_alias_store import (
    canonical_customer_name,
    create_customer_alias,
    customer_alias_map,
    delete_customer_alias,
    list_customer_aliases,
)
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


def test_customer_alias_create_resolve_and_delete(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "registry.db"))
    created = create_customer_alias(
        CustomerAliasInput(
            alias_name="Coast General Hospital",
            canonical_name="Coast General Teaching and Referral Hospital",
        )
    )

    aliases = customer_alias_map()
    assert list_customer_aliases() == [created]
    assert canonical_customer_name("COAST-GENERAL HOSPITAL", aliases) == (
        "Coast General Teaching and Referral Hospital"
    )
    assert delete_customer_alias(created.id) is True
    assert list_customer_aliases() == []


def test_customer_alias_rejects_duplicate_normalized_name(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "registry.db"))
    create_customer_alias(
        CustomerAliasInput(alias_name="Hospital A", canonical_name="Hospital Alpha")
    )

    try:
        create_customer_alias(
            CustomerAliasInput(alias_name="hospital-a", canonical_name="Hospital One")
        )
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate normalized alias should be rejected")
