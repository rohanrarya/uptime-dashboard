# =============================================================================
# Auth Service Module
# Handles credentials verification, stateless HMAC tokens, and 2FA authentication
# =============================================================================

import os
import time
import hmac
import hashlib
from werkzeug.security import check_password_hash
import db
import totp_auth

AUTH_SECRET = os.environ.get('SECRET_KEY') or os.environ.get('ADMIN_PASSWORD') or 'ops-control-center-auth-token-salt-2026'

def generate_token(username):
    """Generates a tamper-proof stateless session token valid for 7 days."""
    expires_at = int(time.time()) + (7 * 86400)
    payload = f"{username}:{expires_at}"
    sig = hmac.new(AUTH_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def validate_token(token):
    """Validates session token signature and expiry without DB lookups."""
    if not token or ':' not in token:
        return None
    parts = token.split(':')
    if len(parts) != 3:
        return None
    username, expires_at_str, sig = parts
    try:
        expires_at = int(expires_at_str)
    except (ValueError, TypeError):
        return None
    if time.time() > expires_at:
        return None
    payload = f"{username}:{expires_at}"
    expected_sig = hmac.new(AUTH_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected_sig):
        return username
    return None


def generate_temp_2fa_token(username):
    """Generates a short-lived (5-minute) pre-auth token for 2FA verification."""
    expires_at = int(time.time()) + 300
    payload = f"2fa_pending:{username}:{expires_at}"
    sig = hmac.new(AUTH_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def validate_temp_2fa_token(token):
    """Validates a temporary 2FA pre-auth token."""
    if not token or ':' not in token:
        return None
    parts = token.split(':')
    if len(parts) != 4 or parts[0] != '2fa_pending':
        return None
    _, username, expires_at_str, sig = parts
    try:
        expires_at = int(expires_at_str)
    except (ValueError, TypeError):
        return None
    if time.time() > expires_at:
        return None
    payload = f"2fa_pending:{username}:{expires_at}"
    expected_sig = hmac.new(AUTH_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected_sig):
        return username
    return None


def verify_login_credentials(username, password):
    """
    Checks user credentials against PostgreSQL (or fallback .env ADMIN_PASSWORD).
    Returns (is_valid, auth_username)
    """
    if not password:
        return False, None

    user = db.get_user(username)
    if not user:
        default_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if password == default_pass:
            return True, 'admin'
        return False, None

    if check_password_hash(user['password_hash'], password):
        return True, user['username']
    return False, None


def authenticate_user(username, password):
    """
    Coordinates Step 1 authentication.
    Checks password, then checks if 2FA is required.
    Returns (response_dict, status_code)
    """
    valid, auth_user = verify_login_credentials(username, password)
    if not valid:
        return {'error': 'Invalid username or password'}, 401

    totp_enabled = db.get_setting('totp_enabled', 'false').lower() == 'true'
    totp_secret = db.get_setting('totp_secret', '').strip()

    # If 2FA is enabled, issue a temporary pre-auth token
    if totp_enabled and totp_secret:
        temp_token = generate_temp_2fa_token(auth_user)
        return {
            'success': True,
            'two_factor_required': True,
            'temp_token': temp_token,
            'message': 'Two-Factor Authentication required. Enter the 6-digit code from your Authenticator app.'
        }, 200

    # Direct login without 2FA
    token = generate_token(auth_user)
    return {
        'success': True,
        'two_factor_required': False,
        'token': token,
        'username': auth_user
    }, 200


def verify_2fa_and_login(temp_token, code):
    """
    Coordinates Step 2 authentication (TOTP 6-digit verification).
    Returns (response_dict, status_code)
    """
    if not code:
        return {'error': '6-digit code is required'}, 400

    username = validate_temp_2fa_token(temp_token)
    if not username:
        return {'error': 'Two-factor session expired. Please log in again.'}, 401

    totp_secret = db.get_setting('totp_secret', '').strip()
    if not totp_secret:
        return {'error': 'Two-factor authentication is not configured.'}, 500

    if not totp_auth.verify_totp(totp_secret, code):
        return {'error': 'Invalid 6-digit code. Check your Authenticator app and try again.'}, 401

    token = generate_token(username)
    return {
        'success': True,
        'token': token,
        'username': username
    }, 200


def get_2fa_status():
    """Returns whether 2FA is currently active."""
    enabled = db.get_setting('totp_enabled', 'false').lower() == 'true'
    return {'enabled': enabled}, 200


def initialize_2fa_setup():
    """Generates a new TOTP secret and QR code for scanning."""
    secret = totp_auth.generate_totp_secret()
    db.set_setting('totp_pending_secret', secret)
    otpauth_url = totp_auth.get_otpauth_url(secret, username='admin', issuer='Ops Command Center')
    qr_url = totp_auth.get_qr_code_url(otpauth_url)
    return {
        'success': True,
        'secret': secret,
        'otpauth_url': otpauth_url,
        'qr_url': qr_url
    }, 200


def confirm_and_enable_2fa(code):
    """Validates the test code from Authenticator app and enables 2FA."""
    secret = db.get_setting('totp_pending_secret', '').strip()
    if not secret:
        return {'error': 'No 2FA setup in progress. Please click Setup 2FA first.'}, 400

    if not totp_auth.verify_totp(secret, code):
        return {'error': 'Invalid 6-digit code. Check your Authenticator app and try again.'}, 400

    db.set_setting('totp_secret', secret)
    db.set_setting('totp_enabled', 'true')
    db.set_setting('totp_pending_secret', '')
    return {'success': True, 'message': 'Google/Microsoft Authenticator 2FA enabled successfully!'}, 200


def disable_2fa(code):
    """Disables 2FA with code or admin password verification."""
    totp_secret = db.get_setting('totp_secret', '').strip()

    if totp_secret and not totp_auth.verify_totp(totp_secret, code):
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if code != admin_pass:
            return {'error': 'Verification failed. Enter current 6-digit code or admin password to disable.'}, 400

    db.set_setting('totp_enabled', 'false')
    db.set_setting('totp_secret', '')
    db.set_setting('totp_pending_secret', '')
    return {'success': True, 'message': 'Two-factor authentication disabled.'}, 200
