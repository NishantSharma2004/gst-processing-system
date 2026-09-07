from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form, HTTPException, Query, Request, Header
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uuid
import os
import io
import asyncio
from datetime import datetime
import time
import sqlite3
from typing import Optional

from .database import init_db, get_db, save_gst_record
from .excel_service import parse_and_clean_excel, generate_3sheet_excel
from .job_runner import run_processing_job, pause_flags
from .auth import hash_password, verify_password, generate_token, verify_token
from .master_service import parse_and_ingest_master_file
from .observability import ClickStackObservabilityMiddleware, telemetry

app = FastAPI(title="GST & Master Corporate Data System")

app.add_middleware(ClickStackObservabilityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

@app.get("/api/observability/metrics")
def get_clickstack_metrics():
    return telemetry.get_metrics()

@app.get("/api/observability/logs")
def get_clickstack_logs():
    return {"logs": list(telemetry.recent_logs)}

@app.post("/v1/logs")
async def receive_otlp_logs(request: Request):
    try:
        payload = await request.json()
        resource_logs = payload.get("resourceLogs", [])
        ingested_count = 0

        for r_log in resource_logs:
            scope_logs = r_log.get("scopeLogs", [])
            for s_log in scope_logs:
                for record in s_log.get("logRecords", []):
                    body = record.get("body", {}).get("stringValue", "")
                    severity = record.get("severityText", "INFO")
                    trace_id = str(uuid.uuid4())[:8]
                    telemetry.record_request("OTLP", "/v1/logs", 200, 0.1, trace_id)
                    ingested_count += 1

        return {"status": "success", "ingested": ingested_count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")

def get_current_user_from_req(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if not token:
        token = request.query_params.get("token")
        
    if token:
        payload = verify_token(token)
        if payload:
            return payload

    return {"user_id": 1, "email": "guest@system.local"}

@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return FileResponse(os.path.join(frontend_path, "index.html"))

@app.api_route("/healthz", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}

# --- AUTH ENDPOINTS ---

@app.post("/api/auth/register")
async def register_user(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    full_name = data.get("full_name", "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email is already registered. Please login.")

    pwd_hash = hash_password(password)
    cursor.execute("INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)", (email, pwd_hash, full_name))
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    token = generate_token(user_id, email)
    return {
        "message": "User registered successfully",
        "access_token": token,
        "token_type": "bearer",
        "user": {"user_id": user_id, "email": email, "full_name": full_name}
    }

@app.post("/api/auth/login")
async def login_user(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="No account found with this email. Please register to create an account.")

    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")

    user_dict = dict(user)
    token = generate_token(user_dict["id"], user_dict["email"])
    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": {"user_id": user_dict["id"], "email": user_dict["email"], "full_name": user_dict["full_name"]}
    }

@app.get("/api/auth/me")
def get_user_profile(request: Request):
    user = get_current_user_from_req(request)
    if user["user_id"] == 1 and user["email"] == "guest@system.local":
        return {"logged_in": False, "user": user}
    return {"logged_in": True, "user": user}

# --- GST BULK PROCESSING ENDPOINTS ---

@app.post("/api/gst/upload")
async def upload_excel(request: Request, file: UploadFile = File(...), selected_column: str = Form(None)):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]

    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Invalid file type. Upload .xlsx, .xls, or .csv")

    contents = await file.read()
    parsed = parse_and_clean_excel(contents, file.filename, selected_column)

    job_id = str(uuid.uuid4())
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO processing_jobs (
            user_id, job_id, filename, total_rows, valid_gst_count, invalid_gst_count,
            unique_gst_count, duplicates_removed, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Created')
    ''', (
        user_id, job_id, file.filename, parsed['total_rows'], parsed['valid_gst_count'],
        parsed['invalid_gst_count'], parsed['unique_gst_count'], parsed['duplicates_removed']
    ))

    for gstin, row_refs in parsed['valid_items'].items():
        cursor.execute('''
            INSERT INTO processing_items (user_id, job_id, gstin, original_row_numbers, status)
            VALUES (?, ?, ?, ?, 'Pending')
        ''', (user_id, job_id, gstin, ",".join(row_refs)))

    for inv in parsed['invalid_items']:
        ref = f"{inv.get('sheet_name','Sheet1')}:R{inv.get('row_num','?')}"
        cursor.execute('''
            INSERT INTO processing_items (user_id, job_id, gstin, original_row_numbers, status, error_type, error_message)
            VALUES (?, ?, ?, ?, 'Invalid', ?, ?)
        ''', (user_id, job_id, inv['gstin'], ref, inv['error_type'], inv['error_message']))

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
        'total_rows': job['total_rows'],
        'valid_gst_count': job['valid_gst_count'],
        'invalid_gst_count': job['invalid_gst_count'],
        'duplicates_removed': job['duplicates_removed'],
        'total_unique': total,
        'processed': processed,
        'successful': job['success_count'],
        'failed': job['failed_count'],
        'pending': total - processed,
        'started_at': job['started_at'],
        'completed_at': job['completed_at']
    }

# --- MASTER CORPORATE DATA ENDPOINTS ---

@app.post("/api/master/upload")
async def upload_master(request: Request, file: UploadFile = File(...)):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]

    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Invalid file type. Upload .xlsx, .xls, or .csv")

    clean_filename = os.path.basename(file.filename)
    try:
        t0 = time.time()
        contents = await file.read()
        res = parse_and_ingest_master_file(contents, clean_filename, user_id)
        duration_sec = time.time() - t0
        telemetry.record_ingestion(clean_filename, res["ingested_records"], duration_sec)

        return {
            "message": "Master corporate data uploaded and indexed successfully",
            "filename": res["filename"],
            "total_input_rows": res["total_input_rows"],
            "ingested_records": res["ingested_records"],
            "duplicates_removed": res.get("duplicates_removed", 0),
            "ingestion_speed": f"{round(res['ingested_records'] / duration_sec, 1)} rows/sec" if duration_sec > 0 else "N/A"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to ingest master file: {str(e)}")

@app.get("/api/master/files")
def get_master_files(request: Request):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    # Backfill any legacy records with null/empty source_file
    cursor.execute("UPDATE company_master_records SET source_file = 'Master_Dataset_1.csv' WHERE source_file IS NULL OR source_file = ''")
    conn.commit()

    cursor.execute('''
        SELECT source_file as filename, COUNT(*) as total_records, MAX(created_at) as created_at
        FROM company_master_records
        WHERE (user_id = ? OR user_id = 1)
        GROUP BY source_file
        ORDER BY created_at DESC
    ''', (user_id,))
    files = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"files": files}

@app.delete("/api/master/files/{filename}")
def delete_master_file(filename: str, request: Request):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]
    clean_filename = os.path.basename(filename)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM company_master_records
        WHERE (user_id = ? OR user_id = 1) AND source_file = ?
    ''', (user_id, clean_filename))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    return {
        "message": f"Dataset '{clean_filename}' deleted successfully",
        "deleted_records": deleted_count
    }

@app.get("/api/master/stats")
def master_stats(request: Request):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM company_master_records WHERE user_id = ? OR user_id = 1", (user_id,))
    cnt = cursor.fetchone()["cnt"]
    conn.close()
    return {"total_master_records": cnt}

@app.get("/api/master/search")
def search_deep_company(request: Request, q: str = Query(..., min_length=2)):
    user = get_current_user_from_req(request)
    user_id = user["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    clean_query = q.strip().upper()
    pattern = f"%{clean_query}%"

    cursor.execute('''
        SELECT * FROM company_master_records 
        WHERE (user_id = ? OR user_id = 1) AND (
            UPPER(company_name) LIKE ? OR UPPER(gstin) LIKE ? OR UPPER(cin) LIKE ? 
            OR UPPER(directors) LIKE ? OR UPPER(pincode) LIKE ? OR UPPER(registration_no) LIKE ?
            OR UPPER(email) LIKE ? OR UPPER(state) LIKE ? OR UPPER(district) LIKE ? OR UPPER(address) LIKE ?
        )
        LIMIT 50
    ''', (user_id, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern))
    master_rows = [dict(r) for r in cursor.fetchall()]

    cursor.execute('''
        SELECT gstin, legal_name, trade_name, gst_status, business_type, last_checked_at 
        FROM gst_records 
        WHERE (user_id = ? OR user_id = 1) AND (
            UPPER(legal_name) LIKE ? OR UPPER(trade_name) LIKE ? OR UPPER(gstin) LIKE ?
        )
        LIMIT 50
    ''', (user_id, pattern, pattern, pattern))
    gst_rows = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        'query': q,
        'total_master_results': len(master_rows),
        'total_gst_results': len(gst_rows),
        'master_results': master_rows,
        'gst_results': gst_rows
    }

@app.get("/api/gst/company-search")
def search_company(request: Request, name: str = Query(..., min_length=2)):
    return search_deep_company(request, q=name)

def purge_old_gst_jobs():
    """Auto-purge completed temporary Tab 1 GST processing items to keep database storage 100% clean."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        if getattr(conn, 'is_postgres', False):
            cursor.execute("DELETE FROM processing_items WHERE created_at < NOW() - INTERVAL '24 hours' OR status = 'Completed'")
            cursor.execute("DELETE FROM processing_jobs WHERE created_at < NOW() - INTERVAL '24 hours'")
        else:
            cursor.execute("DELETE FROM processing_items WHERE job_id IN (SELECT job_id FROM processing_jobs WHERE status = 'Completed')")
        conn.commit()
        conn.close()
    except Exception as e:
        print("[AUTO-CLEANUP LOG]", e, flush=True)

@app.get("/api/gst/export/{job_id}")
def export_excel(job_id: str, background_tasks: BackgroundTasks):
    excel_bytes = generate_3sheet_excel(job_id)
    # Schedule automatic background purge of temporary processing items after user downloads Excel
    background_tasks.add_task(purge_old_gst_jobs)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=GST_Processed_{job_id[:8]}.xlsx"}
    )
