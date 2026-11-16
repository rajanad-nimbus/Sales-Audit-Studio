"""Assign store-scoped users to dynamic Foundation location traits."""

import sqlalchemy as sa
from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "store_assignments",
        sa.Column("location_traits", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "store_location_traits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("location_trait", sa.String(100), nullable=False),
        sa.UniqueConstraint("store_id", "location_trait", name="uq_store_location_trait"),
    )
    op.create_index(
        "ix_store_location_traits_trait",
        "store_location_traits",
        ["location_trait", "store_id"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO store_location_traits (id, store_id, location_trait)
            SELECT md5(r.id || ':' || trait.value), r.code, btrim(trait.value)
            FROM foundation_references AS r
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(r.attributes::jsonb -> 'location_traits') = 'array'
                    THEN r.attributes::jsonb -> 'location_traits'
                    ELSE '[]'::jsonb
                END
            ) AS trait(value)
            WHERE r.category = 'Store' AND r.active IS TRUE AND btrim(trait.value) <> ''
            GROUP BY r.id, r.code, trait.value
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            INSERT INTO store_location_traits (id, store_id, location_trait)
            SELECT lower(hex(randomblob(16))), r.code, trim(trait.value)
            FROM foundation_references AS r,
                 json_each(
                     CASE
                         WHEN json_valid(r.attributes)
                              AND json_type(r.attributes, '$.location_traits') = 'array'
                         THEN json_extract(r.attributes, '$.location_traits')
                         ELSE '[]'
                     END
                 ) AS trait
            WHERE r.category = 'Store' AND r.active = 1 AND trim(trait.value) <> ''
            GROUP BY r.code, trim(trait.value)
            """
        )


def downgrade():
    op.drop_index("ix_store_location_traits_trait", table_name="store_location_traits")
    op.drop_table("store_location_traits")
    op.drop_column("store_assignments", "location_traits")
