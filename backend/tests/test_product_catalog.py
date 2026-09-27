from backend.app.schemas.machine_registry import MachineRegistrationInput
from backend.app.schemas.product_catalog import ProductCatalogInput
from backend.app.services.machine_identity import pcsn_details
from backend.app.services.machine_store import create_registered_machine, list_registered_machines
from backend.app.services.product_catalog_store import (
    create_product_catalog_entry,
    delete_product_catalog_entry,
    list_product_catalog,
    product_catalog_map,
    update_product_catalog_entry,
)


def test_catalog_is_seeded_and_supports_crud(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "catalog.db"))

    assert {item.product_code for item in list_product_catalog()} == {"H19", "H29", "HAL"}
    created = create_product_catalog_entry(
        ProductCatalogInput(product_code="H40", machine_family="Ethos")
    )
    assert created.product_code == "H40"
    updated = update_product_catalog_entry(
        created.id, ProductCatalogInput(product_code="H40", machine_family="Ethos Platform")
    )
    assert updated is not None
    assert updated.machine_family == "Ethos Platform"
    assert delete_product_catalog_entry(created.id) is True
    assert "H40" not in product_catalog_map()


def test_deleted_catalog_entries_are_not_reseeded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "catalog.db"))
    entries = list_product_catalog()
    for item in entries:
        assert delete_product_catalog_entry(item.id) is True

    assert list_product_catalog() == []


def test_longest_product_prefix_wins() -> None:
    details = pcsn_details(
        "H196237", {"H": "Generic H", "H19": "TrueBeam Platform"}
    )

    assert details["product_code"] == "H19"
    assert details["serial_number"] == "6237"
    assert details["model"] == "TrueBeam Platform"


def test_new_catalog_code_is_used_by_machine_registration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "catalog.db"))
    create_product_catalog_entry(
        ProductCatalogInput(product_code="ETH", machine_family="Ethos")
    )
    machine = create_registered_machine(
        MachineRegistrationInput(customer_name="Hospital A", pcsn="ETH1234")
    )

    assert machine.product_code == "ETH"
    assert list_registered_machines()[0].product_code == "ETH"

