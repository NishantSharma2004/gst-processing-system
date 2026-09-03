import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "gst_system.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS gst_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gstin TEXT UNIQUE NOT NULL,
        legal_name TEXT,
        trade_name TEXT,
        gst_status TEXT,
        business_type TEXT,
        provider TEXT DEFAULT 'ClearTax',
        last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_gstin ON gst_records(gstin)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_legal_name ON gst_records(legal_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_name ON gst_records(trade_name)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processing_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        total_rows INTEGER DEFAULT 0,
        valid_gst_count INTEGER DEFAULT 0,
        invalid_gst_count INTEGER DEFAULT 0,
        unique_gst_count INTEGER DEFAULT 0,
        duplicates_removed INTEGER DEFAULT 0,
        processed_count INTEGER DEFAULT 0,
        success_count INTEGER DEFAULT 0,
        failed_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'Created',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP,
        completed_at TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processing_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        gstin TEXT NOT NULL,
        original_row_numbers TEXT,
        status TEXT DEFAULT 'Pending',
        error_type TEXT,
        error_message TEXT,
        retry_count INTEGER DEFAULT 0,
        processed_at TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES processing_jobs (job_id)
    )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_items ON processing_items(job_id, status)')

    conn.commit()
    conn.close()

def get_cached_gst(gstin: str, ttl_days: int = 7):
    conn = get_db()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(days=ttl_days)
    cursor.execute('''
        SELECT * FROM gst_records 
        WHERE gstin = ? AND last_checked_at >= ?
    ''', (gstin, cutoff.strftime("%Y-%m-%d %H:%M:%S")))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def save_gst_record(gstin: str, legal_name: str, trade_name: str, status: str, business_type: str, provider: str = "ClearTax"):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO gst_records (gstin, legal_name, trade_name, gst_status, business_type, provider, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gstin) DO UPDATE SET
            legal_name = excluded.legal_name,
            trade_name = excluded.trade_name,
            gst_status = excluded.gst_status,
            business_type = excluded.business_type,
            provider = excluded.provider,
            last_checked_at = excluded.last_checked_at
    ''', (gstin, legal_name, trade_name, status, business_type, provider, now))
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized!")
