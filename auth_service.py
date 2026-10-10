# =============================================================================
# Auth Service Module
# Handles credentials verification, stateless HMAC tokens, 2FA,
# Multi-user administration, and 1-Click Device/Session Management
# =============================================================================

import os
import time
import hmac
import hashlib
from werkzeug.security import check_password_hash
import db
import totp_auth

AUTH_SECRET = os.environ.get('SECRET_KEY') or os.environ.get('ADMIN_PASSWORD') or 'ops-control-center-auth-token-salt-2026'

def get_token_hash(token):
    """Generates SHA-256 hash of a session token for secure DB storage and lookup."""
    if not token:
        return ''
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def parse_device_info(user_agent_str):
    """Extracts human-readable device and browser name from User-Agent string."""
    if not user_agent_str:
        return 'Unknown Device'
    ua = user_agent_str.lower()
    
    # OS
    os_name = 'Desktop'
    if 'windows' in ua:
        os_name = 'Windows PC'
    elif 'iphone' in ua:
        os_name = 'iPhone'
    elif 'ipad' in ua:
        os_name = 'iPad'
    elif 'android' in ua:
        os_name = 'Android Phone'
    elif 'macintosh' in ua or 'mac os' in ua:
        os_name = 'Mac'
    elif 'linux' in ua:
        os_name = 'Linux PC'
        
    # Browser
    browser = 'Browser'
    if 'edg' in ua:
        browser = 'Edge'
    elif 'chrome' in ua and 'edg' not in ua:
        browser = 'Chrome'
    elif 'safari' in ua and 'chrome' not in ua:
        browser = 'Safari'
    elif 'firefox' in ua:
        browser = 'Firefox'
    elif 'opera' in ua or 'opr' in ua:
        browser = 'Opera'
        
    return f"{os_name} • {browser}"


def generate_token(username, user_agent='', ip_address=''):
    """Generates session token and registers active device session in DB."""
    expires_at = int(time.time()) + (7 * 86400)
    payload = f"{username}:{expires_at}"
    sig = hmac.new(AUTH_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    token = f"{payload}:{sig}"
    
    # Register session in database for device tracking and 1-click revocation
    try:
        token_hash = get_token_hash(token)
        device_info = parse_device_info(user_agent)
        db.create_session(username, token_hash, device_info, ip_address or 'Unknown IP')
    except Exception as e:
        print(f"[Session Notice] Session registration: {e}")
        
    return token


def validate_token(token):
    """Validates session token signature, expiry, and active status in DB."""
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
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Verify session has not been revoked (1-click logout check)
    token_hash = get_token_hash(token)
    if not db.is_session_active(token_hash):
        return None

    db.update_session_activity(token_hash)
    return username


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
    Returns (is_valid, auth_username, role)
    """
    if not password:
        return False, None, None

    user = db.get_user(username)
    if not user:
        default_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if password == default_pass:
            return True, 'admin', 'admin'
        return False, None, None

    if check_password_hash(user['password_hash'], password):
        return True, user['username'], user.get('role', 'admin')
    return False, None, None


def authenticate_user(username, password, user_agent='', ip_address=''):
    """
    Coordinates Step 1 authentication.
    Returns (response_dict, status_code)
    """
    valid, auth_user, role = verify_login_credentials(username, password)
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
    token = generate_token(auth_user, user_agent, ip_address)
    return {
        'success': True,
        'two_factor_required': False,
        'token': token,
        'username': auth_user,
        'role': role
    }, 200


def verify_2fa_and_login(temp_token, code, user_agent='', ip_address=''):
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

    user = db.get_user(username) or {}
    role = user.get('role', 'admin')
    token = generate_token(username, user_agent, ip_address)
    return {
        'success': True,
        'token': token,
        'username': username,
        'role': role
    }, 200


# =============================================================================
# Two-Factor Authentication Management
# =============================================================================

def get_2fa_status():
    enabled = db.get_setting('totp_enabled', 'false').lower() == 'true'
    return {'enabled': enabled}, 200


def initialize_2fa_setup():
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
    totp_secret = db.get_setting('totp_secret', '').strip()
    if totp_secret and not totp_auth.verify_totp(totp_secret, code):
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if code != admin_pass:
            return {'error': 'Verification failed. Enter current 6-digit code or admin password to disable.'}, 400

    db.set_setting('totp_enabled', 'false')
    db.set_setting('totp_secret', '')
    db.set_setting('totp_pending_secret', '')
    return {'success': True, 'message': 'Two-factor authentication disabled.'}, 200


# =============================================================================
# Multi-User Management (Admin Privilege)
# =============================================================================

def list_users():
    """Returns all registered users stored in database."""
    users = db.get_all_users()
    return {'users': users, 'total': len(users)}, 200


def add_user(username, password, role='viewer'):
    """Creates a new user account with administrative or viewer role."""
    clean_user = username.strip().lower()
    clean_pass = password.strip()
    clean_role = role.strip().lower()

    if not clean_user or len(clean_user) < 3:
        return {'error': 'Username must be at least 3 characters long.'}, 400
    if len(clean_pass) < 4:
        return {'error': 'Password must be at least 4 characters long.'}, 400
    if clean_role not in ['admin', 'viewer']:
        clean_role = 'viewer'

    existing = db.get_user(clean_user)
    if existing:
        return {'error': f"User '{clean_user}' already exists in database."}, 400

    new_id = db.create_user(clean_user, clean_pass, clean_role)
    return {'success': True, 'id': new_id, 'message': f"User '{clean_user}' created successfully with role '{clean_role}'!"}, 200


def remove_user(user_id, current_username):
    """Deletes a user account safely."""
    users = db.get_all_users()
    target_user = next((u for u in users if u['id'] == user_id), None)
    if not target_user:
        return {'error': 'User not found.'}, 404

    if target_user['username'].lower() == current_username.lower():
        return {'error': 'You cannot delete your own logged-in account.'}, 400

    # Ensure at least one admin remains
    admin_count = len([u for u in users if u.get('role') == 'admin'])
    if target_user.get('role') == 'admin' and admin_count <= 1:
        return {'error': 'Cannot delete the only remaining administrator account.'}, 400

    db.delete_user(user_id)
    return {'success': True, 'message': f"User '{target_user['username']}' deleted successfully."}, 200


# =============================================================================
# Device & Session Management (1-Click Logout)
# =============================================================================

def list_active_sessions(current_token):
    """Returns all active logged-in devices and marks the current device."""
    current_hash = get_token_hash(current_token)
    sessions = db.get_active_sessions()
    
    for s in sessions:
        s['is_current'] = (s.get('token_hash') == current_hash)
        # Mask token_hash from response
        del s['token_hash']
        
    return {'sessions': sessions, 'total': len(sessions)}, 200


def logout_device(session_id):
    """Revokes a specific device session (1-click logout)."""
    db.revoke_session(session_id)
    return {'success': True, 'message': 'Device logged out successfully.'}, 200


def logout_all_other_devices(current_username, current_token):
    """Revokes all sessions except the current one."""
    current_hash = get_token_hash(current_token)
    db.revoke_all_other_sessions(current_username, current_hash)
    return {'success': True, 'message': 'All other devices logged out successfully.'}, 200


def logout_session(token):
    """Revokes the current session."""
    token_hash = get_token_hash(token)
    if token_hash:
        try:
            conn, engine = db.get_connection()
            cur = conn.cursor()
            if engine == 'postgres':
                cur.execute("UPDATE sessions SET is_active = FALSE WHERE token_hash = %s;", (token_hash,))
            else:
                cur.execute("UPDATE sessions SET is_active = 0 WHERE token_hash = ?;", (token_hash,))
                conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass
    return {'success': True, 'message': 'Logged out successfully.'}, 200
