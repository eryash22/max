from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import Base


def assert_dev_environment() -> None:
    """DEV_DATA_CLEANUP (force=true) purges bypass every entity's own reference/
    status delete-guards via the generic FK-cascade below (force_delete_row) — the
    permission check alone only proves the caller is a Super Admin, not that this is
    a QA/demo deployment. This is the one thing standing between that and an
    accidental or malicious production data-bomb: every force-delete call site must
    call this before touching the database. Blocks whenever ENVIRONMENT=production
    (config.py), regardless of which permission the caller holds."""
    if settings.environment.strip().lower() == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="DEV data cleanup (force-delete) is disabled when ENVIRONMENT=production.",
        )

# Generic Super-Admin cascade purge, driven by the SQLAlchemy metadata's actual FK
# graph instead of a hand-maintained per-entity reference list. The earlier
# entity-by-entity cascades (vendors.py, devices.py) were each hand-written and each
# turned up a real bug once tested against live data (a mismatched FK column that a
# per-table, per-column scoped delete silently missed). Walking the FK graph itself
# instead of guessing which tables matter removes that whole class of bug, and never
# goes stale as new tables/columns are added.
#
# Rule for every table that has a FK into the row being deleted: nullable FK column
# -> detach (set NULL); NOT NULL FK column -> the referencing row is force-deleted
# first (recursively, so its own dependents are purged too), before the row itself.


def _build_fk_graph():
    """{target_table_name: [(referencing_table, referencing_column), ...]}. Iterates
    `Base.metadata.tables` (declaration order) rather than `sorted_tables` — a full
    topological sort isn't needed here (this graph is walked recursively at runtime,
    not used to order DDL), and several tables have mutually dependent FKs
    (branches <-> users <-> departments, ...) that make `sorted_tables` emit a
    SAWarning without actually affecting correctness."""
    graph = defaultdict(list)
    for table in Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                graph[fk.column.table.name].append((table, col))
    return graph


_FK_GRAPH = None


def _fk_graph():
    global _FK_GRAPH
    if _FK_GRAPH is None:
        _FK_GRAPH = _build_fk_graph()
    return _FK_GRAPH


def force_delete_row(db: Session, table_name: str, row_id: str, *, _visited: set[tuple[str, str]] | None = None) -> int:
    """Recursively purges the row identified by `row_id` in `table_name` and every
    row that transitively, non-nullably depends on it. Returns the number of rows
    deleted (the root row included). Does not commit — the caller controls the
    transaction, same as every other force-delete path in this codebase."""
    if _visited is None:
        _visited = set()
    key = (table_name, row_id)
    if key in _visited:
        return 0
    _visited.add(key)

    table = Base.metadata.tables[table_name]
    deleted = 0

    for ref_table, ref_col in _fk_graph().get(table_name, []):
        ref_ids = [r[0] for r in db.execute(select(ref_table.c.id).where(ref_col == row_id))]
        if not ref_ids:
            continue
        if ref_col.nullable:
            db.execute(sa_update(ref_table).where(ref_table.c.id.in_(ref_ids)).values(**{ref_col.name: None}))
        else:
            for ref_id in ref_ids:
                deleted += force_delete_row(db, ref_table.name, ref_id, _visited=_visited)

    db.execute(sa_delete(table).where(table.c.id == row_id))
    deleted += 1
    return deleted
