from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uuid
import os
import io
import asyncio
from datetime import datetime
import sqlite3

from .database import init_db, get_db, save_gst_record
from .excel_service import parse_and_clean_excel, generate_3sheet_excel
from .job_runner import run_processing_job, pause_flags

app = FastAPI(title="GST Processing System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return FileResponse(os.path.join(frontend_path, "index.html"))

@app.api_route("/healthz", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}

@app.post("/api/gst/upload")
async def upload_excel(file: UploadFile = File(...), selected_column: str = Form(None)):
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Invalid file type. Upload .xlsx, .xls, or .csv")

    contents = await file.read()
    parsed = parse_and_clean_excel(contents, file.filename, selected_column)

    job_id = str(uuid.uuid4())
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO processing_jobs (
            job_id, filename, total_rows, valid_gst_count, invalid_gst_count,
            unique_gst_count, duplicates_removed, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Created')
    ''', (
        job_id, file.filename, parsed['total_rows'], parsed['valid_gst_count'],
        parsed['invalid_gst_count'], parsed['unique_gst_count'], parsed['duplicates_removed']
    ))

    for gstin, row_nums in parsed['valid_items'].items():
        cursor.execute('''
            INSERT INTO processing_items (job_id, gstin, original_row_numbers, status)
            VALUES (?, ?, ?, 'Pending')
        ''', (job_id, gstin, ",".join(row_nums)))

    for inv in parsed['invalid_items']:
        cursor.execute('''
            INSERT INTO processing_items (job_id, gstin, original_row_numbers, status, error_type, error_message)
            VALUES (?, ?, ?, 'Invalid', ?, ?)
        ''', (job_id, inv['gstin'], inv['row_num'], inv['error_type'], inv['error_message']))

    conn.commit()
    conn.close()

    return {
        'job_id': job_id,
        'filename': file.filename,
        'total_rows': parsed['total_rows'],
        'valid_gst_count': parsed['valid_gst_count'],
        'invalid_gst_count': parsed['invalid_gst_count'],
        'unique_gst_count': parsed['unique_gst_count'],
        'duplicates_removed': parsed['duplicates_removed'],
        'detected_column': parsed['gst_column'],
        'available_columns': parsed['columns']
    }

@app.post("/api/gst/process/{job_id}")
async def start_process(job_id: str, background_tasks: BackgroundTasks):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,))
    job = cursor.fetchone()
    conn.close()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pause_flags[job_id] = False
    background_tasks.add_task(run_processing_job, job_id)

    return {"message": "Job processing started", "job_id": job_id}

@app.post("/api/gst/pause/{job_id}")
def pause_process(job_id: str):
    pause_flags[job_id] = True
    return {"message": "Pause requested", "job_id": job_id}

@app.get("/api/gst/status/{job_id}")
def get_status(job_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(row)
    total = job['unique_gst_count']
    processed = job['processed_count']
    pct = round((processed / total * 100), 1) if total > 0 else 0.0

    return {
        'job_id': job['job_id'],
        'filename': job['filename'],
        'status': job['status'],
        'progress_pct': pct,
        'total_unique': total,
        'processed': processed,
        'successful': job['success_count'],
        'failed': job['failed_count'],
        'pending': total - processed,
        'started_at': job['started_at'],
        'completed_at': job['completed_at']
    }

@app.get("/api/gst/company-search")
def search_company(name: str = Query(..., min_length=2)):
    conn = get_db()
    cursor = conn.cursor()
    query_str = f"%{name.strip()}%"
    cursor.execute('''
        SELECT gstin, legal_name, trade_name, gst_status, business_type, last_checked_at 
        FROM gst_records 
        WHERE legal_name LIKE ? OR trade_name LIKE ? OR gstin LIKE ?
        LIMIT 50
    ''', (query_str, query_str, query_str))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        'search_query': name,
        'total_results': len(rows),
        'results': rows
    }

@app.get("/api/gst/export/{job_id}")
def export_excel(job_id: str):
    excel_bytes = generate_3sheet_excel(job_id)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=GST_Processed_{job_id[:8]}.xlsx"}
    )
