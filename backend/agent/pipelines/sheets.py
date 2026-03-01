"""Google Sheets pipeline — create and edit spreadsheets via the Sheets API.

Operations are thin async wrappers around the Google Sheets API.
The LLM plans which operations to call; the gsuite_executor runs them.
Write operations support row-by-row animation so the browser screencast
shows data appearing incrementally.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_sheets_service, get_drive_service, _run_sync


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def create_spreadsheet(title: str = "Untitled Spreadsheet") -> dict:
    """Create a new Google Spreadsheet. Returns {spreadsheetId, title, url}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _create():
        return service.spreadsheets().create(
            body={"properties": {"title": title}}
        ).execute()

    ss = await _run_sync(_create)
    ss_id = ss["spreadsheetId"]
    return {
        "spreadsheetId": ss_id,
        "title": ss.get("properties", {}).get("title", title),
        "url": f"https://docs.google.com/spreadsheets/d/{ss_id}/edit",
    }


async def get_spreadsheet(spreadsheet_id: str) -> dict:
    """Read spreadsheet metadata. Returns {spreadsheetId, title, sheets: [{title, rowCount, colCount}]}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _get():
        return service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

    ss = await _run_sync(_get)
    sheets = []
    for sheet in ss.get("sheets", []):
        props = sheet.get("properties", {})
        grid = props.get("gridProperties", {})
        sheets.append({
            "title": props.get("title", ""),
            "sheetId": props.get("sheetId", 0),
            "rowCount": grid.get("rowCount", 0),
            "columnCount": grid.get("columnCount", 0),
        })

    return {
        "spreadsheetId": spreadsheet_id,
        "title": ss.get("properties", {}).get("title", ""),
        "sheets": sheets,
    }


async def read_range(spreadsheet_id: str, range: str = "Sheet1") -> dict:
    """Read cell values from a range. Returns {spreadsheetId, range, values: [[...]]}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _read():
        return service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range,
        ).execute()

    result = await _run_sync(_read)
    return {
        "spreadsheetId": spreadsheet_id,
        "range": result.get("range", range),
        "values": result.get("values", []),
    }


async def _write_single_range(spreadsheet_id: str, range_str: str, values: list[list]) -> dict:
    """Low-level write of a 2D array to a range."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _write():
        return service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()

    return await _run_sync(_write)


async def write_range(spreadsheet_id: str, range: str, values: list[list], _context: dict | None = None) -> dict:
    """Write a 2D array of values to a range.

    When _context is provided and there are multiple rows, writes one row
    at a time with short delays for animated screencast display.

    Returns {spreadsheetId, updatedCells}.
    """
    if _context and len(values) > 1:
        # Animated: write row-by-row
        # Parse the range to get the starting row (e.g., "Sheet1!A1:D10" -> row 1)
        import re
        match = re.search(r'([A-Z]+)(\d+)', range)
        col_letter = match.group(1) if match else "A"
        start_row = int(match.group(2)) if match else 1
        sheet_prefix = range.split("!")[0] + "!" if "!" in range else ""

        total_cells = 0
        for row_idx, row in enumerate(values):
            row_range = f"{sheet_prefix}{col_letter}{start_row + row_idx}"
            result = await _write_single_range(spreadsheet_id, row_range, [row])
            total_cells += result.get("updatedCells", 0)
            await asyncio.sleep(0.25)

        return {
            "spreadsheetId": spreadsheet_id,
            "updatedCells": total_cells,
            "updatedRange": range,
        }

    # Fast path — single call
    result = await _write_single_range(spreadsheet_id, range, values)
    return {
        "spreadsheetId": spreadsheet_id,
        "updatedCells": result.get("updatedCells", 0),
        "updatedRange": result.get("updatedRange", range),
    }


async def append_rows(spreadsheet_id: str, range: str = "Sheet1", values: list[list] | None = None) -> dict:
    """Append rows to the end of data in a range. Returns {spreadsheetId, updatedRows}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    if values is None:
        values = []

    def _append():
        return service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()

    result = await _run_sync(_append)
    updates = result.get("updates", {})
    return {
        "spreadsheetId": spreadsheet_id,
        "updatedRows": updates.get("updatedRows", 0),
        "updatedRange": updates.get("updatedRange", range),
    }


async def create_sheet(spreadsheet_id: str, title: str = "New Sheet") -> dict:
    """Add a new sheet/tab to the spreadsheet. Returns {spreadsheetId, sheetId, title}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _add():
        return service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {"title": title}
                        }
                    }
                ]
            },
        ).execute()

    result = await _run_sync(_add)
    replies = result.get("replies", [{}])
    sheet_props = replies[0].get("addSheet", {}).get("properties", {}) if replies else {}
    return {
        "spreadsheetId": spreadsheet_id,
        "sheetId": sheet_props.get("sheetId", 0),
        "title": sheet_props.get("title", title),
    }


async def format_cells(
    spreadsheet_id: str,
    sheet_id: int = 0,
    start_row: int = 0,
    end_row: int = 1,
    start_col: int = 0,
    end_col: int = 1,
    bold: bool = False,
    bg_color: dict | None = None,
) -> dict:
    """Apply basic formatting to a cell range. Returns {spreadsheetId, formatted}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    cell_format: dict[str, Any] = {}
    fields = []

    if bold:
        cell_format["textFormat"] = {"bold": True}
        fields.append("userEnteredFormat.textFormat.bold")

    if bg_color:
        cell_format["backgroundColor"] = bg_color
        fields.append("userEnteredFormat.backgroundColor")

    if not fields:
        return {"spreadsheetId": spreadsheet_id, "formatted": False}

    def _format():
        return service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row,
                                "endRowIndex": end_row,
                                "startColumnIndex": start_col,
                                "endColumnIndex": end_col,
                            },
                            "cell": {"userEnteredFormat": cell_format},
                            "fields": ",".join(fields),
                        }
                    }
                ]
            },
        ).execute()

    await _run_sync(_format)
    return {"spreadsheetId": spreadsheet_id, "formatted": True}


async def add_formula(spreadsheet_id: str, cell: str = "A1", formula: str = "") -> dict:
    """Write a formula to a specific cell. Returns {spreadsheetId, cell, formula}."""
    service = await get_sheets_service()
    if not service:
        raise RuntimeError("Sheets API not available — not authenticated")

    def _formula():
        return service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=cell,
            valueInputOption="USER_ENTERED",
            body={"values": [[formula]]},
        ).execute()

    await _run_sync(_formula)
    return {"spreadsheetId": spreadsheet_id, "cell": cell, "formula": formula}


async def list_recent_sheets(max_results: int = 10) -> list[dict]:
    """List recent Google Sheets via Drive API."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _list():
        return service.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            orderBy="modifiedTime desc",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
        ).execute()

    result = await _run_sync(_list)
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{f['id']}/edit"),
            "modifiedTime": f.get("modifiedTime", ""),
        }
        for f in result.get("files", [])
    ]


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

SHEETS_OPERATIONS: list[OpDef] = [
    {
        "name": "create_spreadsheet",
        "description": "Create a new Google Spreadsheet",
        "parameters": "title: str",
        "fn": create_spreadsheet,
    },
    {
        "name": "get_spreadsheet",
        "description": "Read spreadsheet metadata (sheet names, dimensions)",
        "parameters": "spreadsheet_id: str",
        "fn": get_spreadsheet,
    },
    {
        "name": "read_range",
        "description": "Read cell values from a range (e.g. 'Sheet1!A1:D10')",
        "parameters": "spreadsheet_id: str, range: str = 'Sheet1'",
        "fn": read_range,
    },
    {
        "name": "write_range",
        "description": "Write a 2D array of values to a range (e.g. 'Sheet1!A1:C3')",
        "parameters": "spreadsheet_id: str, range: str, values: list[list]",
        "fn": write_range,
    },
    {
        "name": "append_rows",
        "description": "Append rows of data to the end of existing data in a sheet",
        "parameters": "spreadsheet_id: str, range: str = 'Sheet1', values: list[list]",
        "fn": append_rows,
    },
    {
        "name": "create_sheet",
        "description": "Add a new sheet/tab to the spreadsheet",
        "parameters": "spreadsheet_id: str, title: str",
        "fn": create_sheet,
    },
    {
        "name": "format_cells",
        "description": "Apply formatting (bold, background color) to a cell range. Uses 0-indexed row/col.",
        "parameters": "spreadsheet_id: str, sheet_id: int = 0, start_row: int = 0, end_row: int = 1, start_col: int = 0, end_col: int = 1, bold: bool = False, bg_color: dict = None",
        "fn": format_cells,
    },
    {
        "name": "add_formula",
        "description": "Write a formula to a specific cell (e.g. '=SUM(A1:A10)')",
        "parameters": "spreadsheet_id: str, cell: str, formula: str",
        "fn": add_formula,
    },
    {
        "name": "list_recent_sheets",
        "description": "List recent Google Sheets spreadsheets",
        "parameters": "max_results: int = 10",
        "fn": list_recent_sheets,
    },
]

SHEETS_OP_MAP = {op["name"]: op["fn"] for op in SHEETS_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class SheetsPipeline(Pipeline):
    pipeline_type = "sheets"
    display_name = "Google Sheets"
    description = (
        "Create and edit Google Sheets spreadsheets. Can create new sheets, "
        "fill in data, create formulas, format cells, and organize data."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgoogle sheets?\b", r"\bspreadsheet\b", r"\bexcel\b",
            r"\bcreate a sheet\b", r"\bedit.{0,10}sheet\b",
        ]
        desc_lower = task_description.lower()
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        task_description = params.get("instruction", params.get("description", ""))
        await plan_and_execute(
            run_id=run_id,
            job_id=job_id,
            pipeline_type="sheets",
            service_name="Google Sheets",
            task_description=task_description,
            available_ops=SHEETS_OPERATIONS,
            op_map=SHEETS_OP_MAP,
        )
