import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from .mapping import SheetConfig, _norm, best_fit_config, match_config

MAX_HEADER_SCAN_ROWS = 6
MIN_HEADER_MATCHES = 2


@dataclass
class ParsedRow:
    row_number: int
    raw: dict[str, object]


@dataclass
class ParsedSheet:
    name: str
    config: SheetConfig | None
    headers: list[str]
    header_row_index: int
    rows: list[ParsedRow] = field(default_factory=list)


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_header_row(all_rows: list[tuple], config: SheetConfig | None) -> tuple[int, list[str]]:
    """Return (0-based row index, header labels) for the most likely header row.

    Templates in this workspace put a title/description row first, so the
    real header is usually row index 1 (second row); we still scan a few
    rows and score against the known column map to stay robust.
    """
    best_index = 0
    best_headers: list[str] = []
    best_score = -1
    expected = set(config.column_map.keys()) if config else set()

    for idx in range(min(MAX_HEADER_SCAN_ROWS, len(all_rows))):
        row = all_rows[idx]
        labels = [str(c).strip() if c is not None else "" for c in row]
        normalized = {_norm(label) for label in labels if label}
        score = len(normalized & expected) if expected else sum(1 for label in labels if label)
        if score > best_score:
            best_score = score
            best_index = idx
            best_headers = labels

    return best_index, best_headers


def parse_workbook(path: Path) -> list[ParsedSheet]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parsed_sheets: list[ParsedSheet] = []

    for sheet_name in workbook.sheetnames:
        worksheet = workbook[sheet_name]
        all_rows = [row for row in worksheet.iter_rows(values_only=True)]
        if not all_rows:
            parsed_sheets.append(ParsedSheet(name=sheet_name, config=None, headers=[], header_row_index=0))
            continue

        config = match_config(sheet_name)
        header_index, headers = _find_header_row(all_rows, config)

        rows: list[ParsedRow] = []
        for offset, data_row in enumerate(all_rows[header_index + 1 :]):
            if all(cell is None for cell in data_row):
                continue
            raw = {}
            for col_index, header in enumerate(headers):
                if not header:
                    continue
                value = data_row[col_index] if col_index < len(data_row) else None
                if isinstance(value, str):
                    value = value.strip() or None
                raw[header] = value
            rows.append(ParsedRow(row_number=header_index + offset + 2, raw=raw))

        parsed_sheets.append(
            ParsedSheet(name=sheet_name, config=config, headers=headers, header_row_index=header_index, rows=rows)
        )

    workbook.close()
    return parsed_sheets


def parse_csv(path: Path, *, display_name: str) -> list[ParsedSheet]:
    """A CSV file has exactly one implicit "sheet" and no sheet-tab name to match
    against `match_config()` the way `.xlsx` sheet tabs are matched — `display_name`
    (the uploaded filename minus extension, e.g. "vendor_master.csv" -> "vendor_master")
    is tried first through the identical `match_config()` path every `.xlsx` sheet uses,
    matching the existing template-naming convention. If that fails (an unrecognized or
    generically-named file), `best_fit_config()` guesses from the header row's column
    overlap instead — a fallback, not the primary detection path, so a well-named file
    never depends on it."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        all_rows = [tuple(row) for row in csv.reader(f)]

    if not all_rows:
        return [ParsedSheet(name=display_name, config=None, headers=[], header_row_index=0)]

    config = match_config(display_name)
    header_index, headers = _find_header_row(all_rows, config)
    if config is None:
        config = best_fit_config(headers)

    rows: list[ParsedRow] = []
    for offset, data_row in enumerate(all_rows[header_index + 1 :]):
        if all(not cell for cell in data_row):
            continue
        raw: dict[str, object] = {}
        for col_index, header in enumerate(headers):
            if not header:
                continue
            value = data_row[col_index] if col_index < len(data_row) else None
            if isinstance(value, str):
                value = value.strip() or None
            raw[header] = value
        rows.append(ParsedRow(row_number=header_index + offset + 2, raw=raw))

    return [ParsedSheet(name=display_name, config=config, headers=headers, header_row_index=header_index, rows=rows)]
