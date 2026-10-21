"""Reusable row-serialization + HTTP-response layer shared by every module's export
endpoint. A caller supplies human-readable `headers` and a matching list of row value
lists (already resolved from real model/schema fields — see PROJECT_STATUS.md's Module
14 export notes for why no raw-column dump is ever used here); this module only handles
turning that into XLSX/CSV bytes and a downloadable HTTP response. Mirrors the layering
of `app/imports/` (parser/mapping/validators split by concern) on the export side.
"""

from typing import Any

import openpyxl
from fastapi import Response

EXPORT_FORMATS = ["xlsx", "csv"]

_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


# CSV/XLSX formula injection (OWASP): a string cell starting with one of these
# characters (or a leading tab/CR) is interpreted as a formula by Excel/Sheets/
# LibreOffice when the export is opened — not literal text — which can execute
# attacker-controlled cell references or, via legacy DDE, OS commands. Every export
# in this app writes user-controlled fields (vendor/branch/asset names, notes, ...)
# straight into cells via this one shared function, so it's the single place that
# needs to neutralize them.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """openpyxl/csv can't write None or arbitrary objects directly — normalize to a
    plain scalar, same "never fabricate, just render nothing" discipline as the rest
    of this codebase's null-handling. Also applies the formula-injection guard above."""
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    text = value if isinstance(value, str) else str(value)
    if text.startswith(_FORMULA_TRIGGER_CHARS):
        # A single leading quote is the standard OWASP mitigation — Excel/Sheets/
        # LibreOffice then treat the whole value as literal text, never a formula.
        return "'" + text
    return text


def rows_to_xlsx_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    import io

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append([_cell(v) for v in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rows_to_csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    # utf-8-sig (BOM) so Excel opens a UTF-8 CSV without mangling non-ASCII text.
    return buffer.getvalue().encode("utf-8-sig")


def build_export_response(*, headers: list[str], rows: list[list[Any]], format: str, filename_stem: str) -> Response:
    if format not in EXPORT_FORMATS:
        raise ValueError(f"format must be one of {', '.join(EXPORT_FORMATS)}")
    content = rows_to_csv_bytes(headers, rows) if format == "csv" else rows_to_xlsx_bytes(headers, rows)
    filename = f"{filename_stem}.{format}"
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
