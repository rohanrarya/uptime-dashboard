# =============================================================================
# TOTP 2-Factor Authentication (RFC 6238)
# Compatible with Google Authenticator, Microsoft Authenticator, Authy, etc.
# Pure Python standard library — zero external dependencies.
# =============================================================================

import os
import time
import base64
import struct
import hmac
import hashlib
import secrets
import urllib.parse

def generate_totp_secret():
    """Generates a secure 160-bit (20-byte) Base32 secret string."""
    return base64.b32encode(secrets.token_bytes(20)).decode('utf-8').replace('=', '')

def get_totp_code(secret, intervals_no=None):
    """Calculates the 6-digit TOTP code for a given secret and 30-second interval."""
    if not secret:
        return ''
    if intervals_no is None:
        intervals_no = int(time.time()) // 30
    
    # Pad base32 string if needed
    padded = secret + '=' * ((8 - len(secret) % 8) % 8)
    try:
        key = base64.b32decode(padded, casefold=True)
    except Exception:
        return ''
    
    msg = struct.pack('>Q', intervals_no)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[19] & 15
    code = (struct.unpack('>I', h[o:o+4])[0] & 0x7fffffff) % 1000000
    return str(code).zfill(6)

def verify_totp(secret, code):
    """
    Verifies a 6-digit code against a secret.
    Allows a +/- 1 step drift window (total 90s window) for slight client clock skew.
    """
    if not secret or not code:
        return False
    
    clean_code = str(code).strip().replace(' ', '')
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False
        
    curr_interval = int(time.time()) // 30
    for offset in (-1, 0, 1):
        if get_totp_code(secret, curr_interval + offset) == clean_code:
            return True
    return False

def get_otpauth_url(secret, username='admin', issuer='Ops Command Center'):
    """Generates standard otpauth:// URL for authenticator apps."""
    label = f"{issuer}:{username}"
    params = {
        'secret': secret,
        'issuer': issuer,
        'algorithm': 'SHA1',
        'digits': '6',
        'period': '30'
    }
    return f"otpauth://totp/{urllib.parse.quote(label)}?{urllib.parse.urlencode(params)}"

def get_qr_code_url(otpauth_url):
    """Generates a secure QR code image URL for scanning with phone camera."""
    encoded = urllib.parse.quote(otpauth_url)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={encoded}"
