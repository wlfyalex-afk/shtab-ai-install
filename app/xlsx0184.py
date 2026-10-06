"""Excel-compatible XLSX writer for operational meeting reports."""
import io
import sys
from datetime import datetime, timezone
from pathlib import Path


VENDOR = Path(__file__).resolve().parent / "vendor0184"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import xlsxwriter


def _created_text(value):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def workbook_bytes(sheets, created_at=None):
    """Build a workbook in memory using XlsxWriter's Office-tested OOXML."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties({
        "title": "Отчёт Штаб.AI", "author": "Штаб.AI", "company": "БИМ-ДВ",
        "created": _created_text(created_at),
    })
    title_format = workbook.add_format({
        "font_name": "Arial", "font_size": 15, "bold": True,
        "font_color": "#0D466E", "valign": "vcenter",
    })
    subtitle_format = workbook.add_format({
        "font_name": "Arial", "font_size": 9, "italic": True,
        "font_color": "#607484", "valign": "vcenter",
    })
    header_format = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "bold": True,
        "font_color": "#FFFFFF", "bg_color": "#16354D",
        "align": "center", "valign": "vcenter", "text_wrap": True,
    })
    body_format = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "font_color": "#172D3D",
        "valign": "top", "text_wrap": True, "bottom": 1, "bottom_color": "#D4DCE2",
    })
    number_format = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "font_color": "#172D3D",
        "align": "right", "valign": "top", "bottom": 1, "bottom_color": "#D4DCE2",
    })
    status_format = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "font_color": "#172D3D",
        "bg_color": "#EAF3F8", "valign": "top", "text_wrap": True,
        "bottom": 1, "bottom_color": "#D4DCE2",
    })

    for sheet_data in sheets:
        worksheet = workbook.add_worksheet(str(sheet_data["name"])[:31])
        headers = list(sheet_data.get("headers") or [])
        rows = list(sheet_data.get("rows") or [])
        widths = list(sheet_data.get("widths") or [])
        last_column = max(0, len(headers) - 1)
        worksheet.hide_gridlines(2)
        worksheet.freeze_panes(4, 0)
        worksheet.set_landscape()
        worksheet.fit_to_pages(1, 0)
        worksheet.set_margins(0.4, 0.4, 0.6, 0.6)
        worksheet.set_row(0, 25)
        worksheet.set_row(1, 20)
        worksheet.set_row(2, 8)
        worksheet.set_row(3, 30)
        if last_column:
            worksheet.merge_range(0, 0, 0, last_column, sheet_data.get("title") or "", title_format)
            worksheet.merge_range(1, 0, 1, last_column, sheet_data.get("subtitle") or "", subtitle_format)
        else:
            worksheet.write(0, 0, sheet_data.get("title") or "", title_format)
            worksheet.write(1, 0, sheet_data.get("subtitle") or "", subtitle_format)
        for column, width in enumerate(widths):
            worksheet.set_column(column, column, width)
        for column, header in enumerate(headers):
            worksheet.write(3, column, header, header_format)
        status_column = sheet_data.get("status_column")
        for row_index, values in enumerate(rows, 4):
            worksheet.set_row(row_index, 34 if any(len(str(value or "")) > 80 for value in values) else 22)
            for column, value in enumerate(values):
                cell_format = status_format if status_column == column + 1 else (
                    number_format if isinstance(value, (int, float)) and not isinstance(value, bool) else body_format
                )
                worksheet.write(row_index, column, value, cell_format)
        if headers:
            worksheet.autofilter(3, 0, max(3, len(rows) + 3), last_column)
    workbook.close()
    return output.getvalue()
