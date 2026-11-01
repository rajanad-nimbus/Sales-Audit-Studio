"""Persist manual-match store scope for indexed authorization filters."""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reconciliation_matches",
        sa.Column("store_id", sa.String(length=50), nullable=True),
    )

    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text("""
            UPDATE reconciliation_matches AS match_row
            SET store_id = resolved.store_id
            FROM (
                SELECT match_row.id, MIN(transaction_row.store_id) AS store_id
                FROM reconciliation_matches AS match_row
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE
                        WHEN jsonb_typeof(match_row.transaction_ids::jsonb) = 'array'
                        THEN match_row.transaction_ids::jsonb
                        ELSE '[]'::jsonb
                    END
                ) AS transaction_ids(transaction_id)
                JOIN canonical_transactions AS transaction_row
                  ON transaction_row.id = transaction_ids.transaction_id
                GROUP BY match_row.id, jsonb_array_length(
                    CASE
                        WHEN jsonb_typeof(match_row.transaction_ids::jsonb) = 'array'
                        THEN match_row.transaction_ids::jsonb
                        ELSE '[]'::jsonb
                    END
                )
                HAVING jsonb_array_length(
                           CASE
                               WHEN jsonb_typeof(match_row.transaction_ids::jsonb) = 'array'
                               THEN match_row.transaction_ids::jsonb
                               ELSE '[]'::jsonb
                           END
                       ) >= 2
                   AND COUNT(DISTINCT transaction_ids.transaction_id) = jsonb_array_length(
                           CASE
                               WHEN jsonb_typeof(match_row.transaction_ids::jsonb) = 'array'
                               THEN match_row.transaction_ids::jsonb
                               ELSE '[]'::jsonb
                           END
                       )
                   AND COUNT(DISTINCT transaction_row.store_id) = 1
            ) AS resolved
            WHERE match_row.id = resolved.id
        """))
    elif dialect == "sqlite":
        op.execute(sa.text("""
            UPDATE reconciliation_matches AS match_row
            SET store_id = (
                SELECT MIN(transaction_row.store_id)
                FROM json_each(
                    CASE
                        WHEN json_valid(match_row.transaction_ids)
                         AND json_type(match_row.transaction_ids) = 'array'
                        THEN match_row.transaction_ids
                        ELSE '[]'
                    END
                ) AS transaction_ids
                JOIN canonical_transactions AS transaction_row
                  ON transaction_row.id = transaction_ids.value
                HAVING json_array_length(
                           CASE
                               WHEN json_valid(match_row.transaction_ids)
                                AND json_type(match_row.transaction_ids) = 'array'
                               THEN match_row.transaction_ids
                               ELSE '[]'
                           END
                       ) >= 2
                   AND COUNT(DISTINCT transaction_ids.value) = json_array_length(
                           CASE
                               WHEN json_valid(match_row.transaction_ids)
                                AND json_type(match_row.transaction_ids) = 'array'
                               THEN match_row.transaction_ids
                               ELSE '[]'
                           END
                       )
                   AND COUNT(DISTINCT transaction_row.store_id) = 1
            )
        """))
    else:
        raise RuntimeError(f"Unsupported database dialect for reconciliation match scope: {dialect}")

    with op.get_context().autocommit_block():
        op.create_index(
            "ix_reconciliation_matches_store_created_id",
            "reconciliation_matches",
            ["store_id", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_reconciliation_matches_store_created_id",
            table_name="reconciliation_matches",
            postgresql_concurrently=True,
        )
    op.drop_column("reconciliation_matches", "store_id")
