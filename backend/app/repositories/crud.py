from sqlalchemy import or_
from sqlalchemy.orm import Session


def _filtered_query(db: Session, model, *, search: str | None, search_fields: list[str] | None, filters: dict):
    query = db.query(model)
    for field, value in filters.items():
        if value is not None:
            query = query.filter(getattr(model, field) == value)
    if search and search_fields:
        like = f"%{search}%"
        query = query.filter(or_(*[getattr(model, f).ilike(like) for f in search_fields]))
    return query


def paginate(db: Session, model, *, page: int, page_size: int, search: str | None = None, search_fields: list[str] | None = None, **filters):
    """`page=1, page_size=<a large export cap>` is also how export endpoints get "every
    row matching the current filters" — `total` (from `.count()`, computed before the
    `.limit()`) is unaffected by the cap, so callers can tell a result was truncated by
    comparing `total` against the `page_size` they asked for. No separate "fetch
    everything" helper is needed for that — see `services/export_service.py`."""
    query = _filtered_query(db, model, search=search, search_fields=search_fields, filters=filters)
    total = query.count()
    items = query.order_by(model.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total
