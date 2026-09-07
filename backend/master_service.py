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

def parse_and_ingest_master_file(file_bytes: bytes, filename: str, user_id: int) -> dict:
    filename = os.path.basename(filename)
    if filename.endswith('.csv'):
        df = pd.read_csv(io.BytesIO(file_bytes), low_memory=False)
        sheets = {'Sheet1': df}
    else:
        xls = pd.ExcelFile(io.BytesIO(file_bytes))
        sheets = {s: pd.read_excel(xls, sheet_name=s) for s in xls.sheet_names}

    total_records = 0
    ingested_records = 0

    conn = get_db()
    cursor = conn.cursor()

    for sheet_name, df in sheets.items():
        if df.empty:
            continue
            
        total_records += len(df)

        col_name = find_column(df, ['company name', 'legal name', 'company', 'name', 'entity name'])
        col_cin = find_column(df, ['cin', 'registration no', 'reg no', 'corporate id'])
        col_gstin = find_column(df, ['gstin', 'gst number', 'gst', 'gstin/uin'])
        col_inc_date = find_column(df, ['incorporation date', 'inc date', 'date of incorporation', 'reg date'])
        col_status = find_column(df, ['company status', 'status', 'active status'])
        col_roc = find_column(df, ['roc', 'roc code', 'registration office'])
        col_reg_no = find_column(df, ['registration number', 'reg number', 'reg no'])
        col_category = find_column(df, ['category', 'company category'])
        col_sub_category = find_column(df, ['sub category', 'sub-category'])
        col_class = find_column(df, ['class', 'class of company', 'type'])
        col_auth_cap = find_column(df, ['authorized capital', 'auth capital', 'authorized cap'])
        col_paid_cap = find_column(df, ['paid capital', 'paid up capital', 'paidup capital'])
        col_listing = find_column(df, ['listing status', 'listed'])
        col_email = find_column(df, ['email', 'email id', 'e-mail'])
        col_address = find_column(df, ['address', 'registered address', 'reg address'])
        col_state = find_column(df, ['state', 'state name'])
        col_district = find_column(df, ['district', 'city'])
        col_pincode = find_column(df, ['pincode', 'pin code', 'zip', 'postal code'])
        col_activity = find_column(df, ['activity', 'business activity', 'industry'])
        col_charges = find_column(df, ['charges', 'mortgages', 'open charges'])
        col_directors = find_column(df, ['director', 'directors', 'din', 'management'])

        batch = []
        for idx, row in df.iterrows():
            c_name = clean_str(row[col_name]) if col_name else ""
            if not c_name:
                for cell in row:
                    val = clean_str(cell)
                    if len(val) > 3 and not val.isdigit():
                        c_name = val
                        break
            if not c_name:
                continue

            cin = clean_str(row[col_cin]) if col_cin else ""
            gstin = clean_str(row[col_gstin]).upper() if col_gstin else ""
            inc_date = clean_str(row[col_inc_date]) if col_inc_date else ""
            status = clean_str(row[col_status]) if col_status else "Active"
            roc = clean_str(row[col_roc]) if col_roc else ""
            reg_no = clean_str(row[col_reg_no]) if col_reg_no else ""
            category = clean_str(row[col_category]) if col_category else ""
            sub_category = clean_str(row[col_sub_category]) if col_sub_category else ""
            class_type = clean_str(row[col_class]) if col_class else ""
            auth_cap = clean_str(row[col_auth_cap]) if col_auth_cap else ""
            paid_cap = clean_str(row[col_paid_cap]) if col_paid_cap else ""
            listing = clean_str(row[col_listing]) if col_listing else ""
            email = clean_str(row[col_email]) if col_email else ""
            address = clean_str(row[col_address]) if col_address else ""
            state = clean_str(row[col_state]) if col_state else ""
            district = clean_str(row[col_district]) if col_district else ""
            pincode = clean_str(row[col_pincode]) if col_pincode else ""
            activity = clean_str(row[col_activity]) if col_activity else ""
            charges = clean_str(row[col_charges]) if col_charges else ""
            directors = clean_str(row[col_directors]) if col_directors else ""

            batch.append((
                user_id, filename, cin, c_name, gstin, inc_date, status, roc, reg_no,
                category, sub_category, class_type, auth_cap, paid_cap, listing,
                email, address, state, district, pincode, activity, charges, directors
            ))

            if len(batch) >= 5000:
                cursor.executemany('''
                    INSERT INTO company_master_records (
                        user_id, source_file, cin, company_name, gstin, incorporation_date, company_status,
                        roc, registration_no, category, sub_category, class_type,
                        authorized_capital, paid_capital, listing_status, email, address,
                        state, district, pincode, activity, charges, directors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', batch)
                conn.commit()
                ingested_records += len(batch)
                batch = []

        if batch:
            cursor.executemany('''
                INSERT INTO company_master_records (
                    user_id, source_file, cin, company_name, gstin, incorporation_date, company_status,
                    roc, registration_no, category, sub_category, class_type,
                    authorized_capital, paid_capital, listing_status, email, address,
                    state, district, pincode, activity, charges, directors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
            ingested_records += len(batch)

    conn.close()

    return {
        'filename': filename,
        'total_input_rows': total_records,
        'ingested_records': ingested_records
    }
