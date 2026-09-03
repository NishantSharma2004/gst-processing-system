import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import re
import io
import os
import sqlite3
from .database import get_db

GST_REGEX = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$')

def clean_gstin(val) -> str:
    if pd.isna(val):
        return ""
    val_str = str(val).strip().upper()
    val_str = re.sub(r'\s+', '', val_str)
    return val_str

def parse_and_clean_excel(file_bytes: bytes, filename: str, selected_column: str = None):
    if filename.endswith('.csv'):
        df = pd.read_csv(io.BytesIO(file_bytes))
    else:
        df = pd.read_excel(io.BytesIO(file_bytes))

    total_rows = len(df)

    gst_col = selected_column
    if not gst_col or gst_col not in df.columns:
        for col in df.columns:
            col_str = str(col).lower()
            if 'gst' in col_str or 'number' in col_str or 'pin' in col_str or 'code' in col_str:
                gst_col = col
                break
        if not gst_col and len(df.columns) > 0:
            gst_col = df.columns[0]

    valid_items = {}
    invalid_items = []

    for idx, row in df.iterrows():
        row_num = idx + 2
        raw_val = row[gst_col]
        cleaned = clean_gstin(raw_val)

        if not cleaned:
            continue

        if GST_REGEX.match(cleaned):
            if cleaned not in valid_items:
                valid_items[cleaned] = []
            valid_items[cleaned].append(str(row_num))
        else:
            invalid_items.append({
                'gstin': cleaned if cleaned else str(raw_val),
                'row_num': str(row_num),
                'error_type': 'Invalid GSTIN Format',
                'error_message': 'Does not match 15-character GSTIN pattern'
            })

    unique_gst_count = len(valid_items)
    duplicates_removed = sum(len(rows) - 1 for rows in valid_items.values())
    valid_gst_count = sum(len(rows) for rows in valid_items.values())
    invalid_gst_count = len(invalid_items)

    return {
        'gst_column': gst_col,
        'columns': [str(c) for c in df.columns],
        'total_rows': total_rows,
        'valid_gst_count': valid_gst_count,
        'invalid_gst_count': invalid_gst_count,
        'unique_gst_count': unique_gst_count,
        'duplicates_removed': duplicates_removed,
        'valid_items': valid_items,
        'invalid_items': invalid_items
    }

def generate_3sheet_excel(job_id: str) -> bytes:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,))
    job = dict(cursor.fetchone())

    cursor.execute("SELECT * FROM processing_items WHERE job_id = ?", (job_id,))
    items = [dict(r) for r in cursor.fetchall()]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    border = Border(left=Side(style='thin', color='D9D9D9'),
                    right=Side(style='thin', color='D9D9D9'),
                    top=Side(style='thin', color='D9D9D9'),
                    bottom=Side(style='thin', color='D9D9D9'))

    # Sheet 1: GST Details
    ws1 = wb.create_sheet(title='GST Details')
    headers1 = ['GST Number', 'Legal Name', 'Trade Name', 'GST Status', 'Business Type']
    ws1.append(headers1)

    for item in items:
        if item['status'] == 'Success':
            cursor.execute("SELECT * FROM gst_records WHERE gstin = ?", (item['gstin'],))
            rec = cursor.fetchone()
            if rec:
                ws1.append([
                    rec['gstin'],
                    rec['legal_name'] or 'Not Available',
                    rec['trade_name'] or 'Not Available',
                    rec['gst_status'] or 'Not Available',
                    rec['business_type'] or 'Not Available'
                ])
            else:
                ws1.append([item['gstin'], 'Not Available', 'Not Available', 'Not Available', 'Not Available'])

    # Sheet 2: Errors
    ws2 = wb.create_sheet(title='Errors')
    headers2 = ['GST Number', 'Error Type', 'Error Message', 'Retry Count', 'Status']
    ws2.append(headers2)

    for item in items:
        if item['status'] in ['Failed', 'Invalid']:
            ws2.append([
                item['gstin'],
                item['error_type'] or 'Search Error',
                item['error_message'] or 'GST details unavailable',
                item['retry_count'],
                item['status']
            ])

    # Sheet 3: Summary
    ws3 = wb.create_sheet(title='Summary')
    ws3.append(['Metric Name', 'Metric Value'])
    ws3.append(['Filename', job['filename']])
    ws3.append(['Total Input Rows', job['total_rows']])
    ws3.append(['Valid GST Numbers', job['valid_gst_count']])
    ws3.append(['Invalid GST Numbers', job['invalid_gst_count']])
    ws3.append(['Unique GST Numbers Searched', job['unique_gst_count']])
    ws3.append(['Duplicates Removed', job['duplicates_removed']])
    ws3.append(['Successfully Processed', job['success_count']])
    ws3.append(['Failed / Not Found', job['failed_count']])
    ws3.append(['Job Status', job['status']])
    ws3.append(['Created At', str(job['created_at'])])
    ws3.append(['Completed At', str(job['completed_at']) if job['completed_at'] else 'N/A'])

    for ws in [ws1, ws2, ws3]:
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for col_num, col_cells in enumerate(ws.iter_cols(min_row=1, max_row=ws.max_row), 1):
            max_len = 0
            for cell in col_cells:
                if cell.row == 1:
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                else:
                    cell.border = border
                    cell.alignment = Alignment(vertical='center')
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            col_letter = get_column_letter(col_num)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

    conn.close()

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
