import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

from models import Base


def assert_migration_schema_matches_models(sql: str) -> None:
    """Catch drift in columns, nullability, types, defaults, indexes, and constraints."""
    columns_by_table = {}
    foreign_keys = set()
    unique_constraints = set()
    unique_constraint_names = {}
    check_constraints = set()
    identifier = r'(?:"([^"]+)"|([a-zA-Z_]\w*))'

    def record_column(table: str, name: str, definition: str) -> None:
        not_null = bool(re.search(r"\bNOT NULL\b", definition))
        type_name = re.split(
            r"\s+(?:NOT NULL|NULL|DEFAULT|PRIMARY KEY|UNIQUE|REFERENCES)\b",
            definition.rstrip(",; "),
            maxsplit=1,
        )[0].strip()
        default = _default_value(definition, type_name)
        columns_by_table.setdefault(table, {})[name] = (type_name, not_null, default)

    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.S):
        for line in body.splitlines():
            match = re.match(r"\s+" + identifier + r"\s+(.+)", line)
            if match and (match.group(1) or match.group(2)).upper() not in {
                "PRIMARY", "FOREIGN", "CONSTRAINT", "UNIQUE", "CHECK"
            }:
                record_column(table, match.group(1) or match.group(2), match.group(3))
        for constrained, referred, referred_columns, actions in re.findall(
            r"FOREIGN KEY\s*\(([^)]+)\)\s*REFERENCES\s+(\w+)\s*\(([^)]+)\)([^,\n)]*)",
            body,
            re.I,
        ):
            foreign_keys.add(_foreign_key_signature(table, constrained, referred, referred_columns, actions))
        for constraint_name, constrained in re.findall(
            r"(?:CONSTRAINT\s+(\w+)\s+)?UNIQUE\s*\(([^)]+)\)", body, re.I
        ):
            names = _column_names(constrained)
            unique_constraints.add((table, names))
            # PostgreSQL names unnamed table-level unique constraints this way.
            generated_name = constraint_name or f"{table}_{'_'.join(names)}_key"
            unique_constraint_names[(table, generated_name)] = (table, names)
        check_constraints.update(
            (table, name, _normalize_check(expression))
            for name, expression in _named_check_constraints(body)
        )

    check_constraints.update(
        (table, name, _normalize_check(expression))
        for table, name, expression in _alter_table_checks(sql)
    )

    for match in re.finditer(
        r"ALTER TABLE (\w+) ADD COLUMN " + identifier + r"\s+([^;]+);", sql
    ):
        record_column(match.group(1), match.group(2) or match.group(3), match.group(4))

    for table, constrained, referred, referred_columns, actions in re.findall(
        r"ALTER TABLE\s+(\w+)\s+ADD(?: CONSTRAINT\s+\w+)?\s+FOREIGN KEY\s*"
        r"\(([^)]+)\)\s+REFERENCES\s+(\w+)\s*\(([^)]+)\)([^;]*);",
        sql,
        re.I,
    ):
        foreign_keys.add(_foreign_key_signature(table, constrained, referred, referred_columns, actions))
    for table, constraint_name, constrained in re.findall(
        r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+(\w+)\s+UNIQUE\s*\(([^)]+)\)",
        sql,
        re.I,
    ):
        names = _column_names(constrained)
        unique_constraints.add((table, names))
        unique_constraint_names[(table, constraint_name)] = (table, names)
    for table, constraint_name in re.findall(
        r"ALTER TABLE\s+(\w+)\s+DROP CONSTRAINT\s+(\w+)", sql, re.I
    ):
        unique_constraints.discard(unique_constraint_names.get((table, constraint_name)))

    for table, column in re.findall(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) DROP NOT NULL", sql
    ):
        type_name, _, default = columns_by_table[table][column]
        columns_by_table[table][column] = (type_name, False, default)
    for table, column in re.findall(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) SET NOT NULL", sql
    ):
        type_name, _, default = columns_by_table[table][column]
        columns_by_table[table][column] = (type_name, True, default)

    for match in re.finditer(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) (SET DEFAULT\s+([^;]+)|DROP DEFAULT)",
        sql,
        re.I,
    ):
        table, column = match.group(1), match.group(2)
        type_name, not_null, _ = columns_by_table[table][column]
        default_expression = match.group(4)
        default = _normalize_default(default_expression, type_name) if default_expression else None
        columns_by_table[table][column] = (type_name, not_null, default)

    model_tables = set(Base.metadata.tables)
    assert set(columns_by_table) - {"alembic_version"} == model_tables
    default_mismatches = []
    for table in Base.metadata.tables.values():
        assert set(columns_by_table[table.name]) == {column.name for column in table.columns}
        for column in table.columns:
            actual_type, actual_not_null, actual_default = columns_by_table[table.name][column.name]
            expected_type = str(column.type.compile(dialect=postgresql.dialect()))
            assert actual_type.upper().replace(" ", "") == expected_type.upper().replace(" ", ""), (
                table.name, column.name, actual_type, expected_type
            )
            assert actual_not_null == (not column.nullable), (table.name, column.name)
            expected_default = _default_value(
                str(CreateColumn(column).compile(dialect=postgresql.dialect())), expected_type
            )
            if actual_default != expected_default:
                default_mismatches.append((table.name, column.name, actual_default, expected_default))
    assert not default_mismatches, default_mismatches

    migration_indexes = {
        (table, name, _column_names(columns), bool(unique), _normalize_predicate(predicate))
        for unique, name, table, columns, predicate in re.findall(
            r"CREATE (UNIQUE )?INDEX (?:CONCURRENTLY )?(\w+) ON (\w+) \(([^)]+)\)(?: WHERE (.*?))?;",
            sql,
            re.I,
        )
    }
    model_indexes = {
        (
            table.name,
            index.name,
            tuple(column.name for column in index.columns),
            index.unique,
            _normalize_predicate(
                str(index.dialect_options["postgresql"]["where"].compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                ))
                if index.dialect_options["postgresql"]["where"] is not None
                else None
            ),
        )
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    assert migration_indexes == model_indexes

    model_foreign_keys = {
        (
            table.name,
            (foreign_key.parent.name,),
            foreign_key.column.table.name,
            (foreign_key.column.name,),
            foreign_key.ondelete,
            foreign_key.onupdate,
            foreign_key.deferrable,
            foreign_key.initially,
        )
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == model_foreign_keys

    model_checks = {
        (table.name, constraint.name, _normalize_check(str(constraint.sqltext)))
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_constraints == model_checks

    migration_unique = set(unique_constraints)
    migration_unique.update(
        (table, _column_names(columns))
        for unique, _name, table, columns in re.findall(
            r"CREATE (UNIQUE )?INDEX (?:CONCURRENTLY )?(\w+) ON (\w+) \(([^)]+)\)", sql
        )
        if unique
    )
    model_unique = {
        (table.name, (column.name,))
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.unique
    }
    model_unique.update(
        (table.name, tuple(column.name for column in constraint.columns))
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    model_unique.update(
        (table.name, tuple(column.name for column in index.columns))
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.unique
    )
    assert migration_unique == model_unique


def _column_names(value: str) -> tuple[str, ...]:
    return tuple(column.strip().strip('"') for column in value.split(","))


def _normalize_default(value: str, type_name: str | None = None) -> str:
    normalized = value.strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        if _matching_parenthesis(normalized, 0) != len(normalized) - 1:
            break
        normalized = normalized[1:-1].strip()
    if normalized.startswith("'") and normalized.endswith("'"):
        normalized = normalized[1:-1].replace("''", "'")
    if type_name and type_name.upper().startswith("BOOLEAN"):
        normalized = {"1": "true", "0": "false"}.get(normalized, normalized)
    return normalized.lower() if normalized.lower() in {"true", "false", "null"} else normalized


def _default_value(definition: str, type_name: str | None = None) -> str | None:
    match = re.search(
        r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT NULL|NULL|PRIMARY KEY|UNIQUE|REFERENCES)\b|$)",
        definition.rstrip(",; "),
        re.I,
    )
    return _normalize_default(match.group(1), type_name) if match else None


def _normalize_check(value: str) -> str:
    """Normalize formatting without changing the case-sensitive contents of SQL literals."""
    return re.sub(r"\s+", " ", value.strip()).strip("() ")


def _matching_parenthesis(value: str, opening: int) -> int:
    depth = 0
    quote = None
    index = opening
    while index < len(value):
        character = value[index]
        if quote:
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AssertionError("Unclosed CHECK constraint expression in migration SQL")


def _named_check_constraints(statement: str) -> list[tuple[str, str]]:
    constraints = []
    for match in re.finditer(r"(?:CONSTRAINT\s+(\w+)\s+)?CHECK\s*\(", statement, re.I):
        opening = statement.find("(", match.start())
        closing = _matching_parenthesis(statement, opening)
        constraints.append((match.group(1) or "", statement[opening + 1:closing]))
    return constraints


def _alter_table_checks(sql: str) -> list[tuple[str, str, str]]:
    constraints = []
    pattern = re.compile(
        r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+(\w+)\s+CHECK\s*\(", re.I
    )
    for match in pattern.finditer(sql):
        opening = sql.find("(", match.start())
        closing = _matching_parenthesis(sql, opening)
        constraints.append((match.group(1), match.group(2), sql[opening + 1:closing]))
    return constraints


def _normalize_predicate(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value.strip().strip("() ")).lower()


def _foreign_key_signature(
    table: str, constrained: str, referred: str, referred_columns: str, actions: str
) -> tuple:
    on_delete = re.search(r"\bON DELETE\s+(\w+)", actions, re.I)
    on_update = re.search(r"\bON UPDATE\s+(\w+)", actions, re.I)
    deferrable = bool(re.search(r"\bDEFERRABLE\b", actions, re.I))
    initially = re.search(r"\bINITIALLY\s+(DEFERRED|IMMEDIATE)", actions, re.I)
    return (
        table,
        _column_names(constrained),
        referred,
        _column_names(referred_columns),
        on_delete.group(1).upper() if on_delete else None,
        on_update.group(1).upper() if on_update else None,
        deferrable if deferrable else None,
        initially.group(1).upper() if initially else None,
    )


def test_alembic_can_load_migrations_without_application_pythonpath():
    repository_root = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "heads"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    heads = [line for line in result.stdout.splitlines() if "(head)" in line]
    assert len(heads) == 1
    assert heads[0].split()[0].isdigit()


def test_postgresql_migration_chain_renders_offline():
    repository_root = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["DATABASE_URL"] = "postgresql+asyncpg://offline:offline@localhost/offline"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head", "--sql"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "CREATE UNIQUE INDEX uq_batch_runs_one_running_per_date" in result.stdout
    assert "CREATE UNIQUE INDEX uq_recommendations_one_proposed_per_case" in result.stdout
    assert "CREATE UNIQUE INDEX uq_gl_cross_reference_default_by_type" in result.stdout
    assert "CREATE UNIQUE INDEX ix_audit_total_definitions_name_revision" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_audit_events_case_seq" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_audit_events_object_seq" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_evidence_snapshots_case_retrieved_id" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_transaction_adjustments_created_id" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_transaction_adjustments_status_created_id" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_source_records_system_delivery" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_source_records_system_record_id" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_ct_pending_return_link" in result.stdout
    assert "CREATE INDEX CONCURRENTLY ix_reconciliation_matches_store_created_id" in result.stdout
    assert "ADD COLUMN store_id VARCHAR(50)" in result.stdout
    assert "transaction_type = 'Return' AND original_transaction_id IS NULL" in result.stdout
    assert "ck_transaction_adjustments_status" in result.stdout
    assert "status IN ('Proposed', 'Approved', 'Rejected')" in result.stdout
    assert result.stdout.count("ON CONFLICT (name) DO NOTHING") == 2
    assert "version_num='0052'" in result.stdout
    assert "version_num='0057'" in result.stdout
    assert "version_num='0058'" in result.stdout
    assert "ck_reconciliation_matches_status" in result.stdout
    trigger_body = result.stdout.rsplit(
        "CREATE OR REPLACE FUNCTION audit_events_before_insert()", 1
    )[1].split("$$ LANGUAGE plpgsql", 1)[0]
    assert trigger_body.index("pg_advisory_xact_lock") < trigger_body.index("nextval(")
    assert_migration_schema_matches_models(result.stdout)
