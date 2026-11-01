from sqlalchemy.exc import IntegrityError

from apps.exports.routes import (
    _is_duplicate_destination_name,
    _is_export_reservation_conflict,
    _is_sink_receipt_conflict,
)


def test_destination_name_conflict_matches_sqlite_constraint():
    error = IntegrityError(
        "insert", {}, Exception("UNIQUE constraint failed: export_destinations.name")
    )

    assert _is_duplicate_destination_name(error)


def test_export_reservation_matches_idempotency_key_constraint():
    error = IntegrityError(
        "insert", {}, Exception("UNIQUE constraint failed: export_batches.idempotency_key")
    )

    assert _is_export_reservation_conflict(error)


def test_export_reservation_matches_item_reservation_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception(
            "UNIQUE constraint failed: export_items.destination_id, export_items.dataset, "
            "export_items.object_id, export_items.object_version"
        ),
    )

    assert _is_export_reservation_conflict(error)


def test_unrelated_export_integrity_error_is_not_misclassified():
    error = IntegrityError("insert", {}, Exception("FOREIGN KEY constraint failed"))

    assert not _is_export_reservation_conflict(error)


def test_sink_receipt_conflict_matches_idempotency_primary_key():
    error = IntegrityError(
        "insert",
        {},
        Exception("UNIQUE constraint failed: test_sink_receipts.idempotency_key"),
    )

    assert _is_sink_receipt_conflict(error)


def test_unrelated_sink_integrity_error_is_not_a_retry():
    error = IntegrityError("insert", {}, Exception("FOREIGN KEY constraint failed"))

    assert not _is_sink_receipt_conflict(error)
