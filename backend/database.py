import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "gst_system.db"))
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')

    # 2. GST Records table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS gst_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT 1,
        gstin TEXT NOT NULL,
        legal_name TEXT,
        trade_name TEXT,
        gst_status TEXT,
        business_type TEXT,
        provider TEXT DEFAULT 'ClearTax',
        last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 3. Processing Jobs table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processing_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT 1,
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

    # 4. Processing Items table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processing_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT 1,
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

    # Check and add user_id column to tables if migrating existing DB
    for table_name in ['gst_records', 'processing_jobs', 'processing_items']:
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = [r['name'] for r in cursor.fetchall()]
        if 'user_id' not in cols:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN user_id INTEGER DEFAULT 1")

    # Indexes after column verification
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gstin ON gst_records(gstin)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_gstin ON gst_records(user_id, gstin)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_legal_name ON gst_records(legal_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_jobs ON processing_jobs(user_id, job_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_items ON processing_items(job_id, status)')

    # 5. Company Master Records Table (Deep Corporate Data)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS company_master_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source_file TEXT,
        cin TEXT,
        company_name TEXT NOT NULL,
        gstin TEXT,
        incorporation_date TEXT,
        company_status TEXT,
        roc TEXT,
        registration_no TEXT,
        category TEXT,
        sub_category TEXT,
        class_type TEXT,
        authorized_capital TEXT,
        paid_capital TEXT,
        listing_status TEXT,
        email TEXT,
        address TEXT,
        state TEXT,
        district TEXT,
        pincode TEXT,
        activity TEXT,
        charges TEXT,
        directors TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    # Check source_file column migration
    cursor.execute("PRAGMA table_info(company_master_records)")
    cols = [r['name'] for r in cursor.fetchall()]
    if 'source_file' not in cols:
        cursor.execute("ALTER TABLE company_master_records ADD COLUMN source_file TEXT")

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_user ON company_master_records(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_source ON company_master_records(user_id, source_file)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_name ON company_master_records(company_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_gstin ON company_master_records(gstin)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_cin ON company_master_records(cin)')

    conn.commit()
    conn.close()

def get_cached_gst(gstin: str, user_id: int = 1, ttl_days: int = 7):
    conn = get_db()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(days=ttl_days)
    cursor.execute('''
        SELECT * FROM gst_records 
        WHERE (user_id = ? OR user_id = 1) AND gstin = ? AND last_checked_at >= ?
    ''', (user_id, gstin, cutoff.strftime("%Y-%m-%d %H:%M:%S")))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def save_gst_record(gstin: str, legal_name: str, trade_name: str, status: str, business_type: str, provider: str = "ClearTax", user_id: int = 1):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("SELECT id FROM gst_records WHERE user_id = ? AND gstin = ?", (user_id, gstin))
    existing = cursor.fetchone()
    if existing:
        cursor.execute('''
            UPDATE gst_records SET
                legal_name = ?, trade_name = ?, gst_status = ?, business_type = ?, provider = ?, last_checked_at = ?
            WHERE id = ?
        ''', (legal_name, trade_name, status, business_type, provider, now, existing['id']))
    else:
        cursor.execute('''
            INSERT INTO gst_records (user_id, gstin, legal_name, trade_name, gst_status, business_type, provider, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, gstin, legal_name, trade_name, status, business_type, provider, now))
        
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized!")
