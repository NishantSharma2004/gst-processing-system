import hashlib
import os
import secrets
import json
import base64
import time
from typing import Optional, Dict

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "gst_system_secure_secret_key_2026_super_safe")

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + pwd_hash).decode('utf-8')

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        data = base64.b64decode(stored_hash.encode('utf-8'))
        salt = data[:16]
        expected_hash = data[16:]
        actual_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return secrets.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False

def generate_token(user_id: int, email: str, expires_in_seconds: int = 86400 * 30) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + expires_in_seconds
    }
    
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    
    signature_base = f"{header_b64}.{payload_b64}"
    signature = hashlib.pbkdf2_hmac('sha256', signature_base.encode(), SECRET_KEY.encode(), 1000)
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

def verify_token(token: str) -> Optional[Dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        
        header_b64, payload_b64, signature_b64 = parts
        signature_base = f"{header_b64}.{payload_b64}"
        expected_sig = hashlib.pbkdf2_hmac('sha256', signature_base.encode(), SECRET_KEY.encode(), 1000)
        expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")
        
        if not secrets.compare_digest(signature_b64, expected_sig_b64):
            return None
            
        rem = len(payload_b64) % 4
        if rem > 0:
            payload_b64 += "=" * (4 - rem)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        
        if payload.get("exp", 0) < time.time():
            return None
            
        return payload
    except Exception:
        return None
