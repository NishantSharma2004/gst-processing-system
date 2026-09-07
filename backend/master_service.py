import pandas as pd
import io
import re
import os
import sqlite3
from datetime import datetime
from .database import get_db

def clean_str(val) -> str:
    if pd.isna(val) or val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ["nan", "null", "none", "n/a"] else s

def find_column(df, patterns):
    for col in df.columns:
        col_clean = str(col).lower().replace("_", " ").replace("-", " ").strip()
        for p in patterns:
            if p in col_clean:
                return col
    return None

def find_columns_multi(df, patterns):
    matched = []
    for col in df.columns:
        col_clean = str(col).lower().replace("_", " ").replace("-", " ").strip()
        for p in patterns:
            if p in col_clean:
                if col not in matched:
                    matched.append(col)
                break
    return matched

def process_df_chunk(df, filename, user_id, cursor):
    col_name = find_column(df, ['company name', 'legal name', 'company', 'entity name'])
    col_trade = find_column(df, ['trade name', 'brand name', 'trade'])
    col_cin = find_column(df, ['cin', 'registration no', 'reg no', 'corporate id'])
    col_gstin = find_column(df, ['gstin', 'gst number', 'gst', 'gstin/uin'])
    col_inc_date = find_column(df, ['incorporation date', 'inc date', 'date of incorporation', 'reg date'])
    col_status = find_column(df, ['company status', 'status', 'active status', 'gst status'])
    col_roc = find_column(df, ['roc', 'roc code', 'registration office'])
    col_reg_no = find_column(df, ['registration number', 'reg number', 'reg no'])
    col_category = find_column(df, ['category', 'company category', 'business type'])
    col_sub_category = find_column(df, ['sub category', 'sub-category'])
    col_class = find_column(df, ['class', 'class of company', 'type'])
    col_auth_cap = find_column(df, ['authorized capital', 'auth capital', 'authorized cap'])
    col_paid_cap = find_column(df, ['paid capital', 'paid up capital', 'paidup capital'])
    col_listing = find_column(df, ['listing status', 'listed'])
    col_state = find_column(df, ['state', 'state name'])
    col_district = find_column(df, ['district', 'city'])
    col_pincode = find_column(df, ['pincode', 'pin code', 'zip', 'postal code'])
    col_activity = find_column(df, ['activity', 'business activity', 'industry'])
    col_charges = find_column(df, ['charges', 'mortgages', 'open charges'])

    # Multi-column collectors for fields that may appear multiple times (Email 1, Email 2, Phone 1, Phone 2, Director 1, Director 2)
    cols_email = find_columns_multi(df, ['email', 'e-mail', 'mail'])
    cols_phone = find_columns_multi(df, ['phone', 'mobile', 'contact', 'cell', 'telephone'])
    cols_directors = find_columns_multi(df, ['director', 'directors', 'din', 'management', 'partner'])
    cols_address = find_columns_multi(df, ['address', 'registered address', 'reg address', 'office address', 'location'])

    batch = []
    records = df.to_dict('records')
    for row in records:
        gstin = clean_str(row.get(col_gstin)) if col_gstin else ""
        c_name = clean_str(row.get(col_name)) if col_name else ""
        trade_name = clean_str(row.get(col_trade)) if col_trade else ""

        if c_name.lower() in ["not available", "n/a", "none", "nan", "not found"]:
            c_name = ""
        if trade_name.lower() in ["not available", "n/a", "none", "nan", "not found"]:
            trade_name = ""

        if not c_name and trade_name:
            c_name = trade_name
        elif c_name and trade_name and trade_name.lower() not in c_name.lower():
            c_name = f"{c_name} ({trade_name})"

        if not c_name and gstin:
            c_name = f"GST Record {gstin}"

        if not c_name:
            for val_raw in row.values():
                val = clean_str(val_raw)
                if len(val) > 3 and not val.isdigit() and val.lower() not in ["not available", "n/a", "none", "nan"]:
                    c_name = val
                    break
        if not c_name:
            continue

        cin = clean_str(row.get(col_cin)) if col_cin else ""
        inc_date = clean_str(row.get(col_inc_date)) if col_inc_date else ""
        status = clean_str(row.get(col_status)) if col_status else "Active"
        roc = clean_str(row.get(col_roc)) if col_roc else ""
        reg_no = clean_str(row.get(col_reg_no)) if col_reg_no else ""
        category = clean_str(row.get(col_category)) if col_category else ""
        sub_category = clean_str(row.get(col_sub_category)) if col_sub_category else ""
        class_type = clean_str(row.get(col_class)) if col_class else ""
        auth_cap = clean_str(row.get(col_auth_cap)) if col_auth_cap else ""
        paid_cap = clean_str(row.get(col_paid_cap)) if col_paid_cap else ""
        listing = clean_str(row.get(col_listing)) if col_listing else ""
        state = clean_str(row.get(col_state)) if col_state else ""
        district = clean_str(row.get(col_district)) if col_district else ""
        pincode = clean_str(row.get(col_pincode)) if col_pincode else ""
        activity = clean_str(row.get(col_activity)) if col_activity else ""
        charges = clean_str(row.get(col_charges)) if col_charges else ""

        # Collect all email values (Email 1, Email 2)
        emails = []
        for c in cols_email:
            v = clean_str(row.get(c))
            if v and v not in emails:
                emails.append(v)
        email_str = " | ".join(emails)

        # Collect all phone values (Phone 1, Phone 2, Mobile)
        phones = []
        for c in cols_phone:
            v = clean_str(row.get(c))
            if v and v not in phones:
                phones.append(v)
        phone_str = " | ".join(phones)

        if phone_str:
            if email_str:
                email_str = f"{email_str} (Phone: {phone_str})"
            else:
                email_str = f"Phone: {phone_str}"

        # Collect all address values
        addresses = []
        for c in cols_address:
            v = clean_str(row.get(c))
            if v and v not in addresses:
                addresses.append(v)
        address_str = ", ".join(addresses)

        # Collect all director values (Director 1, Director 2, DIN)
        directors_list = []
        for c in cols_directors:
            v = clean_str(row.get(c))
            if v and v not in directors_list:
                directors_list.append(v)
        directors_str = " | ".join(directors_list)

        batch.append((
            user_id, filename, cin, c_name, gstin, inc_date, status, roc, reg_no,
            category, sub_category, class_type, auth_cap, paid_cap, listing,
            email_str, address_str, state, district, pincode, activity, charges, directors_str
        ))

    if batch:
        is_pg = getattr(cursor, 'is_postgres', False)
        if is_pg:
            try:
                import psycopg2.extras
                sql = '''
                    INSERT INTO company_master_records (
                        user_id, source_file, cin, company_name, gstin, incorporation_date, company_status,
                        roc, registration_no, category, sub_category, class_type,
                        authorized_capital, paid_capital, listing_status, email, address,
                        state, district, pincode, activity, charges, directors
                    ) VALUES %s
                '''
                psycopg2.extras.execute_values(cursor.cursor, sql, batch, page_size=2000)
            except Exception:
                cursor.executemany('''
                    INSERT INTO company_master_records (
                        user_id, source_file, cin, company_name, gstin, incorporation_date, company_status,
                        roc, registration_no, category, sub_category, class_type,
                        authorized_capital, paid_capital, listing_status, email, address,
                        state, district, pincode, activity, charges, directors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', batch)
        else:
            cursor.executemany('''
                INSERT INTO company_master_records (
                    user_id, source_file, cin, company_name, gstin, incorporation_date, company_status,
                    roc, registration_no, category, sub_category, class_type,
                    authorized_capital, paid_capital, listing_status, email, address,
                    state, district, pincode, activity, charges, directors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)

    return len(batch)

def parse_and_ingest_master_file(file_bytes: bytes, filename: str, user_id: int) -> dict:
    filename = os.path.basename(filename)
    total_records = 0
    ingested_records = 0

    conn = get_db()
    cursor = conn.cursor()

    if not getattr(conn, 'is_postgres', False):
        try:
            cursor.execute('PRAGMA synchronous = NORMAL')
            cursor.execute('PRAGMA journal_mode = WAL')
        except Exception:
            pass

    if filename.endswith('.csv'):
        chunk_iter = pd.read_csv(io.BytesIO(file_bytes), chunksize=5000, low_memory=False, on_bad_lines='skip')
        for df_chunk in chunk_iter:
            if df_chunk.empty:
                continue
            total_records += len(df_chunk)
            ingested_records += process_df_chunk(df_chunk, filename, user_id, cursor)
            conn.commit()
    else:
        xls = pd.ExcelFile(io.BytesIO(file_bytes))
        for sheet_name in xls.sheet_names:
            if sheet_name.lower() in ['summary', 'errors', 'metrics', 'stats']:
                continue
            df_full = pd.read_excel(xls, sheet_name=sheet_name)
            if df_full.empty:
                continue
            total_records += len(df_full)
            # Process Excel sheet in 5000 row chunks to limit RAM
            for i in range(0, len(df_full), 5000):
                df_chunk = df_full.iloc[i:i+5000]
                ingested_records += process_df_chunk(df_chunk, filename, user_id, cursor)
                conn.commit()

    conn.close()

    return {
        'filename': filename,
        'total_input_rows': total_records,
        'ingested_records': ingested_records
    }
