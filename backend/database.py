import sqlite3
import os
import re
from datetime import datetime, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "gst_system.db"))

IS_POSTGRES = False
if DATABASE_URL and (DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")):
    IS_POSTGRES = True
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    if "ap-south-1" in DATABASE_URL and "cdrannqbwxauktmtzzel" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("ap-south-1", "ap-southeast-1")

class UnifiedCursor:
    def __init__(self, conn, is_postgres=False):
        self.conn = conn
        self.is_postgres = is_postgres
        if is_postgres:
            from psycopg2.extras import RealDictCursor
            self.cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            self.cursor = conn.cursor()

    def execute(self, sql, params=()):
        if self.is_postgres:
            sql = re.sub(r'\?', '%s', sql)
            sql = sql.replace("AUTOINCREMENT", "")
            sql = sql.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        self.cursor.execute(sql, params)
        return self

    def executemany(self, sql, params=()):
        if self.is_postgres:
            sql = re.sub(r'\?', '%s', sql)
        self.cursor.executemany(sql, params)
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        if self.is_postgres:
            try:
                self.cursor.execute("SELECT lastval()")
                res = self.cursor.fetchone()
                return res['lastval'] if res else 1
            except Exception:
                return 1
        return self.cursor.lastrowid

class UnifiedConnection:
    def __init__(self, conn, is_postgres=False):
        self.conn = conn
        self.is_postgres = is_postgres

    def cursor(self):
        return UnifiedCursor(self.conn, self.is_postgres)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def get_db():
    if IS_POSTGRES:
        try:
            import psycopg2
            raw_conn = psycopg2.connect(DATABASE_URL)
            return UnifiedConnection(raw_conn, is_postgres=True)
        except Exception as e:
            print(f"[DATABASE WARNING] PostgreSQL connection error: {e}. Falling back to SQLite.", flush=True)
            db_dir = os.path.dirname(DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            raw_conn = sqlite3.connect(DB_PATH)
            raw_conn.row_factory = sqlite3.Row
            return UnifiedConnection(raw_conn, is_postgres=False)
    else:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        raw_conn = sqlite3.connect(DB_PATH)
        raw_conn.row_factory = sqlite3.Row
        return UnifiedConnection(raw_conn, is_postgres=False)

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    if not IS_POSTGRES:
        try:
            cursor.execute('PRAGMA page_size = 4096')
            cursor.execute('PRAGMA journal_mode = WAL')
            cursor.execute('PRAGMA synchronous = NORMAL')
        except Exception:
            pass

    # 1. Users table
    try:
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
        conn.commit()
    except Exception as e:
        print("[DB INIT WARNING] Users table creation:", e, flush=True)

    # 2. GST Records table
    try:
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
        conn.commit()
    except Exception as e:
        print("[DB INIT WARNING] GST Records table creation:", e, flush=True)

    # 3. Processing Jobs table
    try:
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
        conn.commit()
    except Exception as e:
        print("[DB INIT WARNING] Processing Jobs table creation:", e, flush=True)

    # 4. Processing Items table
    try:
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
            processed_at TIMESTAMP
        )
        ''')
        conn.commit()
    except Exception as e:
        print("[DB INIT WARNING] Processing Items table creation:", e, flush=True)

    # Indexes
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_gstin ON gst_records(gstin)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_gstin ON gst_records(user_id, gstin)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_legal_name ON gst_records(legal_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_jobs ON processing_jobs(user_id, job_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_items ON processing_items(job_id, status)')
        conn.commit()
    except Exception as e:
        pass

    # 5. Company Master Records Table (Deep Corporate Data)
    try:
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_user ON company_master_records(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_source ON company_master_records(user_id, source_file)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_name ON company_master_records(company_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_gstin ON company_master_records(gstin)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_master_cin ON company_master_records(cin)')
        conn.commit()
    except Exception as e:
        print("[DB INIT WARNING] Master records table creation:", e, flush=True)

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
    return row

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
