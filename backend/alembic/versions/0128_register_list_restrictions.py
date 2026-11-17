"""Use exact register lists instead of the channel stand-in on the remote sales and tax totals."""

import json

from alembic import context, op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None

RESTRICTIONS = {
    "5f90e619-de8a-5f13-910b-286323977714": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "e6f96805-9e31-542a-876e-5b4c2c089a88": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "0053c174-3cfd-5768-a22b-59d5d313307b": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "12d26da6-db6a-539e-a190-c9ef9f0ea341": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "0b35321c-e83b-5105-8784-b3bc9a9ca765": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "not_in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "ef30fc0c-f541-548c-b250-629ff383725e": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "not_in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "56340f8b-7dc8-5892-bb80-462ec6d8e87b": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        }
    ],
    "dba778ef-d7ba-5c6e-a665-e44bc0092d04": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "not_in",
            "value": "892,895,896,897",
        }
    ],
    "2548f6e1-ccca-5b17-82a0-4d5e7f77ec79": [
        {"realm": "Transaction", "field": "register_id", "operator": "in", "value": "892,897"}
    ],
    "520bc408-62ec-5773-9ad6-ebf9db0261d1": [
        {"realm": "Transaction", "field": "register_id", "operator": "in", "value": "895,896"}
    ],
}


def upgrade():
    postgres = context.is_offline_mode() or op.get_bind().dialect.name == "postgresql"
    for identifier, restrictions in RESTRICTIONS.items():
        literal = json.dumps(restrictions).replace("'", "''")
        value = f"'{literal}'::json" if postgres else f"'{literal}'"
        op.execute(
            f"update audit_total_definitions set restrictions = {value} "
            f"where id = '{identifier}' and updated_by = 'system'"
        )


def downgrade():
    """Register lists cannot be expressed before this revision; the totals keep the lists."""
