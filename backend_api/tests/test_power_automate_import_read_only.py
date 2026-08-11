from __future__ import annotations

import hashlib
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.sharepoint_audit_service import (
    LocalReadOnlyImportProvider,
    assert_source_operation_allowed,
    classify_cir_document,
    list_import_folders,
    mark_audit_item_indexed,
    mark_matching_items_memory_removed,
    require_index_confirmation,
    run_sharepoint_audit,
)
from core.config import settings


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    }


def test_power_automate_copy_folder_is_bit_for_bit_read_only_and_deduplicated(tmp_path: Path):
    source = tmp_path / "power_automate_inbox"
    (source / "Dossiers sans logique" / "Lot 18").mkdir(parents=True)
    (source / "Autres clients").mkdir(parents=True)
    hidden_cir = source / "Dossiers sans logique" / "Lot 18" / "document_8742.txt"
    hidden_cir.write_text(
        """Dossier justificatif du Crédit d'impôt recherche — version finale
Entreprise : Société Fictive
Projet : Capteur X
Année : 2024
État de l'art
Verrou scientifique et technique
Travaux de recherche réalisés
Résultats obtenus
""",
        encoding="utf-8",
    )
    (source / "Autres clients" / "planning.txt").write_text(
        "Client : Démo\nPlanning contractuel et compte rendu de réunion.",
        encoding="utf-8",
    )
    before = _snapshot(source)
    audit_root = tmp_path / "audit_output"
    provider = LocalReadOnlyImportProvider(source, provider_name="fake")

    first = run_sharepoint_audit(
        provider_name="fake",
        initiated_by="pytest",
        provider=provider,
        audit_root=audit_root,
    )
    second = run_sharepoint_audit(
        provider_name="fake",
        initiated_by="pytest-second-run",
        provider=provider,
        audit_root=audit_root,
    )

    assert first["ok"] is True
    assert first["source_integrity_verified"] is True
    assert first["source_write_operations"] == 0
    assert first["source_create_operations"] == 0
    assert first["source_update_operations"] == 0
    assert first["source_move_operations"] == 0
    assert first["source_delete_operations"] == 0
    assert first["memory_index_operations"] == 0
    assert first["counts"]["new_content"] == 2
    assert second["counts"]["new_content"] == 0
    assert second["counts"]["deduplicated"] == 2
    assert _snapshot(source) == before
    assert first["counts"]["cir_final_confirmed"] == 1
    assert first["counts"]["client_document"] == 1
    cir_item = next(item for item in first["items"] if item["classification"] == "cir_final_confirmed")
    assert cir_item["name"] == "document_8742.txt"
    assert cir_item["detected_identity"] == {
        "organisme": "Société Fictive",
        "project": "Capteur X",
        "year": "2024",
    }


def test_classifier_distinguishes_final_draft_and_client_document():
    final = classify_cir_document(
        "Crédit d'impôt recherche. Dossier justificatif. État de l'art. "
        "Verrou scientifique et technique. Travaux de recherche réalisés. Version finale."
    )
    draft = classify_cir_document(
        "Crédit d'impôt recherche. État de l'art. Verrou technique. "
        "Travaux de recherche réalisés. Brouillon à relire."
    )
    client = classify_cir_document("Compte rendu client, planning et livrables contractuels.")
    assert final["classification"] == "cir_final_confirmed"
    assert draft["classification"] == "cir_draft"
    assert client["classification"] == "client_document"


def test_create_update_move_delete_and_unconfirmed_index_are_rejected():
    for operation in ("list", "read", "hash"):
        assert_source_operation_allowed(operation)
    for operation in ("create", "update", "rename", "move", "delete"):
        with pytest.raises(PermissionError):
            assert_source_operation_allowed(operation)
    with pytest.raises(PermissionError):
        require_index_confirmation("oui")
    require_index_confirmation("INDEXER_DANS_MEMORY_V2")


def test_memory_removal_only_updates_local_audit_metadata(tmp_path: Path):
    source = tmp_path / "inbox"
    source.mkdir()
    document = source / "cir.txt"
    document.write_text(
        "Crédit d'impôt recherche. Version finale. Entreprise : Acme. Projet : Radar. Année : 2024. "
        "État de l'art. Verrou scientifique. Travaux de recherche réalisés.",
        encoding="utf-8",
    )
    before = _snapshot(source)
    audit_root = tmp_path / "audit"
    run = run_sharepoint_audit(
        provider_name="local_read_only",
        initiated_by="pytest",
        provider=LocalReadOnlyImportProvider(source),
        audit_root=audit_root,
    )
    item = run["items"][0]
    mark_audit_item_indexed(
        run["scan_id"],
        item["external_id"],
        result={"ok": True, "chunks_count": 3, "cards_count": 3},
        identity={"organisme": "Acme", "project": "Radar", "year": "2024"},
        audit_root=audit_root,
    )

    result = mark_matching_items_memory_removed(
        "Acme", "Radar", "2024", audit_root=audit_root
    )

    assert result["audit_items_updated"] == 1
    assert result["source_modified"] is False
    assert _snapshot(source) == before


def test_folder_browser_and_scoped_scan_are_read_only(monkeypatch, tmp_path: Path):
    root = tmp_path / "clients"
    selected = root / "Client A" / "Projet Radar" / "2024"
    sibling = root / "Client B" / "Projet Secret" / "2023"
    selected.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (selected / "cir_final.txt").write_text(
        "Crédit d'impôt recherche. Version finale. État de l'art. Verrou scientifique. Travaux réalisés.",
        encoding="utf-8",
    )
    (sibling / "autre.txt").write_text("Ne doit pas être scanné", encoding="utf-8")
    before = _snapshot(root)
    monkeypatch.setattr(settings, "POWER_AUTOMATE_IMPORT_ROOT", str(root))

    clients = list_import_folders(parent="", provider_name="inbox")
    assert [folder["name"] for folder in clients["folders"]] == ["Client A", "Client B"]
    projects = list_import_folders(parent="Client A", provider_name="inbox")
    assert [folder["name"] for folder in projects["folders"]] == ["Projet Radar"]
    with pytest.raises(ValueError):
        list_import_folders(parent="../outside", provider_name="inbox")

    run = run_sharepoint_audit(
        provider_name="inbox",
        relative_folder="Client A/Projet Radar/2024",
        initiated_by="pytest",
        audit_root=tmp_path / "audit",
    )

    assert run["ok"] is True
    assert run["source_scope"] == "Client A/Projet Radar/2024"
    assert run["counts"]["audited"] == 1
    assert run["items"][0]["name"] == "cir_final.txt"
    assert _snapshot(root) == before
