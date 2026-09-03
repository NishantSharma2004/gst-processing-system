import asyncio
import sqlite3
import time
from datetime import datetime
from .database import get_db, get_cached_gst, save_gst_record
from .provider import ClearTaxGSTProvider

provider = ClearTaxGSTProvider()

active_jobs = {}
pause_flags = {}

async def run_processing_job(job_id: str):
    conn = get_db()
    cursor = conn.cursor()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE processing_jobs SET status = 'Processing', started_at = ? WHERE job_id = ?", (now_str, job_id))
    conn.commit()

    cursor.execute("SELECT * FROM processing_items WHERE job_id = ? AND status IN ('Pending', 'Failed')", (job_id,))
    items = [dict(r) for r in cursor.fetchall()]
    conn.close()

    print(f"Starting processing job {job_id} with {len(items)} items...", flush=True)

    # Pass 1: Main Loop
    for item in items:
        if pause_flags.get(job_id, False):
            print(f"Job {job_id} paused by user.", flush=True)
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE processing_jobs SET status = 'Paused' WHERE job_id = ?", (job_id,))
            conn.commit()
            conn.close()
            return

        gstin = item['gstin']

        # Check local cache first
        cached = get_cached_gst(gstin)
        if cached and cached.get('legal_name'):
            conn = get_db()
            cursor = conn.cursor()
            now_item = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Decrease failed_count if this item was previously failed
            if item['status'] == 'Failed':
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET failed_count = MAX(0, failed_count - 1), success_count = success_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))
            else:
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET processed_count = processed_count + 1, success_count = success_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))

            cursor.execute('''
                UPDATE processing_items 
                SET status = 'Success', processed_at = ? 
                WHERE id = ?
            ''', (now_item, item['id']))

            conn.commit()
            conn.close()
            continue

        # Fetch from ClearTax Provider
        result = await asyncio.to_thread(provider.search_by_gstin, gstin)

        conn = get_db()
        cursor = conn.cursor()
        now_item = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if result['success']:
            save_gst_record(
                result['gstin'],
                result['legal_name'],
                result['trade_name'],
                result['gst_status'],
                result['business_type'],
                result['provider']
            )
            if item['status'] == 'Failed':
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET failed_count = MAX(0, failed_count - 1), success_count = success_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))
            else:
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET processed_count = processed_count + 1, success_count = success_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))

            cursor.execute('''
                UPDATE processing_items 
                SET status = 'Success', processed_at = ? 
                WHERE id = ?
            ''', (now_item, item['id']))
        else:
            if item['status'] != 'Failed':
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET processed_count = processed_count + 1, failed_count = failed_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))

            cursor.execute('''
                UPDATE processing_items 
                SET status = 'Failed', error_type = ?, error_message = ?, retry_count = retry_count + 1, processed_at = ? 
                WHERE id = ?
            ''', (result['error_type'], result['error_message'], now_item, item['id']))

        conn.commit()
        conn.close()

    # Pass 2: Automatic Auto-Retry Pass for any Failed items after a short cooldown
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM processing_items WHERE job_id = ? AND status = 'Failed'", (job_id,))
    failed_items = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if failed_items and not pause_flags.get(job_id, False):
        print(f"Auto-retrying {len(failed_items)} failed items after 10s cooldown...", flush=True)
        await asyncio.sleep(10)

        for item in failed_items:
            if pause_flags.get(job_id, False):
                break
            gstin = item['gstin']
            result = await asyncio.to_thread(provider.search_by_gstin, gstin)

            if result['success']:
                save_gst_record(
                    result['gstin'],
                    result['legal_name'],
                    result['trade_name'],
                    result['gst_status'],
                    result['business_type'],
                    result['provider']
                )
                conn = get_db()
                cursor = conn.cursor()
                now_item = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute('''
                    UPDATE processing_items SET status = 'Success', processed_at = ? WHERE id = ?
                ''', (now_item, item['id']))
                cursor.execute('''
                    UPDATE processing_jobs 
                    SET failed_count = MAX(0, failed_count - 1), success_count = success_count + 1 
                    WHERE job_id = ?
                ''', (job_id,))
                conn.commit()
                conn.close()

    # Job Completion
    conn = get_db()
    cursor = conn.cursor()
    completed_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE processing_jobs SET status = 'Completed', completed_at = ? WHERE job_id = ?", (completed_str, job_id))
    conn.commit()
    conn.close()
    print(f"Job {job_id} completed successfully!", flush=True)
