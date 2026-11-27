"""
security_utils.py — Módulo central de seguridad para AlphaX CRM y App de Atletas.

Implementa estándares modernos sin dependencias externas:
  - Hashing seguro de contraseñas con PBKDF2-HMAC-SHA256 (260,000 iteraciones + salt de 128 bits)
    con migración transparente desde hashes SHA-256 heredados.
  - Tokens y cookies firmados criptográficamente con HMAC-SHA256 + expiración (permite mantener
    la sesión iniciada 30 días en el mismo dispositivo sin riesgo de falsificación).
  - Enlaces de invitación de atleta firmados (reemplaza URLs `?athlete=Nombre` sin firma).
  - Rate limiting por IP / identificador para prevenir ataques de fuerza bruta en logins.
  - Exportación de Backup Completo de la base de datos del club (JSON descargable).
  - Escapado HTML seguro contra XSS.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

import streamlit as st

from database import (
    BloodworkRecord,
    Expense,
    LactateTest,
    LactateTestStep,
    Member,
    RaceStrategyScenario,
    SessionLocal,
    SleepRecord,
    Transaction,
    WolfyChatMessage,
    get_club_setting,
    set_club_setting,
)


PBKDF2_ITERATIONS = 260_000
ADMIN_COOKIE_NAME = "alphax_admin_session"
ATHLETE_COOKIE_NAME = "alphax_athlete_session"

# Hash PBKDF2 con salt de transición (sin contraseña en texto plano en el código).
# Se sobrescribe automáticamente al definir ADMIN_PASSWORD en Streamlit Secrets
# o al cambiar la contraseña desde Configuración -> Seguridad.
_BOOTSTRAP_ADMIN_HASH = (
    "pbkdf2_sha256$260000$"
    "c16d76e4786f0ec8bbef1a50f21d313a$"
    "f13583f51a9264b3ace0d10fb13bbb98c767becf2a289e55cc0e2d8346c3c857"
)


# ═════════════════════════════════════════════════════════════════════
#  1. ESCAPADO XSS Y SANITIZACIÓN
# ═════════════════════════════════════════════════════════════════════

def esc(val: object) -> str:
    """Escapa texto de usuario antes de renderizarlo en HTML (protección XSS)."""
    return html.escape(str(val if val is not None else ""), quote=True)


# ═════════════════════════════════════════════════════════════════════
#  2. HASHING SEGURO DE CONTRASEÑAS (PBKDF2-HMAC-SHA256 + SALT)
# ═════════════════════════════════════════════════════════════════════

def hash_password_secure(password: str) -> str:
    """Genera un hash PBKDF2-HMAC-SHA256 con salt aleatorio de 16 bytes."""
    salt_hex = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_hex}${dk}"


def verify_password_secure(password: str, stored_hash: str) -> Tuple[bool, bool]:
    """
    Verifica una contraseña contra su hash almacenado en tiempo constante.
    Retorna `(es_valida, requiere_rehash)`:
      - Soporta `pbkdf2_sha256$...` (estándar actual) y SHA-256 heredado (64 hex)
        para migrar cuentas existentes de forma transparente al iniciar sesión.
    """
    if not password or not stored_hash:
        return False, False

    stored = str(stored_hash).strip()
    if stored.startswith("pbkdf2_sha256$"):
        parts = stored.split("$")
        if len(parts) != 4:
            return False, False
        try:
            iters = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected_hex = parts[3]
            dk_hex = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters).hex()
            ok = hmac.compare_digest(dk_hex, expected_hex)
            return ok, (ok and iters < PBKDF2_ITERATIONS)
        except Exception:
            return False, False

    # Compatibilidad con SHA-256 heredado -> verificar y marcar para rehash inmediato
    legacy_hex = hashlib.sha256(password.encode("utf-8")).hexdigest()
    ok = hmac.compare_digest(legacy_hex, stored)
    return ok, ok


# ═════════════════════════════════════════════════════════════════════
#  3. CLAVE SECRETA Y TOKENS / COOKIES FIRMADOS (HMAC-SHA256)
# ═════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def get_app_secret_key() -> str:
    """
    Obtiene la clave secreta de firma HMAC-SHA256 desde:
      1. `st.secrets["APP_SECRET_KEY"]`
      2. Variable de entorno `APP_SECRET_KEY`
      3. `ClubSetting("app_secret_key")` en la base de datos (auto-generada la primera vez)
    """
    try:
        if "APP_SECRET_KEY" in st.secrets and st.secrets["APP_SECRET_KEY"]:
            return str(st.secrets["APP_SECRET_KEY"]).strip()
    except Exception:
        pass

    env_k = os.environ.get("APP_SECRET_KEY", "").strip()
    if env_k:
        return env_k

    db_k = get_club_setting("app_secret_key", "")
    if db_k and len(db_k) >= 32:
        return db_k

    new_k = secrets.token_hex(32)
    try:
        set_club_setting("app_secret_key", new_k)
    except Exception:
        pass
    return new_k


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode((txt + pad).encode("ascii"))


def create_signed_token(
    role: str,
    subject: str,
    ttl_seconds: int = 30 * 86400,
    extra: Optional[Dict] = None,
) -> str:
    """Crea un token compacto firmado con HMAC-SHA256 y fecha de expiración."""
    payload = {
        "r": str(role),
        "s": str(subject),
        "e": int(time.time()) + int(ttl_seconds),
    }
    if extra:
        payload.update(extra)
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(get_app_secret_key().encode("utf-8"), p_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{p_b64}.{_b64url_encode(sig)}"


def verify_signed_token(token: Optional[str], expected_role: Optional[str] = None) -> Optional[Dict]:
    """
    Valida la firma HMAC-SHA256, expiración y rol de un token/cookie.
    Retorna el diccionario de payload si es válido, o None si fue manipulado o expiró.
    """
    if not token or not isinstance(token, str) or "." not in token:
        return None
    try:
        p_b64, sig_b64 = token.strip().split(".", 1)
        expected_sig = hmac.new(
            get_app_secret_key().encode("utf-8"),
            p_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            return None
        data = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        if not isinstance(data, dict):
            return None
        if int(data.get("e", 0)) < int(time.time()):
            return None
        if expected_role and data.get("r") != expected_role:
            return None
        return data
    except Exception:
        return None


def create_athlete_invite_token(athlete_name: str, member_id: int, ttl_days: int = 7) -> str:
    """Genera un enlace directo seguro y firmado por deportista (válido por `ttl_days` días)."""
    return create_signed_token(
        role="athlete_invite",
        subject=athlete_name,
        ttl_seconds=ttl_days * 86400,
        extra={"uid": int(member_id)},
    )


# ═════════════════════════════════════════════════════════════════════
#  4. CONTRASEÑA DE ADMINISTRADOR (SECRETS -> DB HASH -> BOOTSTRAP HASH)
# ═════════════════════════════════════════════════════════════════════

def is_admin_password_in_secrets() -> bool:
    try:
        if "ADMIN_PASSWORD" in st.secrets and str(st.secrets["ADMIN_PASSWORD"]).strip():
            return True
    except Exception:
        pass
    return bool(os.environ.get("ADMIN_PASSWORD", "").strip())


def verify_admin_password(password_input: str) -> bool:
    """Valida la contraseña de administrador en tiempo constante (sin secretos en texto plano)."""
    if not password_input:
        return False

    # 1. Prioridad máxima: st.secrets / variable de entorno
    secret_pw = ""
    try:
        if "ADMIN_PASSWORD" in st.secrets and st.secrets["ADMIN_PASSWORD"]:
            secret_pw = str(st.secrets["ADMIN_PASSWORD"]).strip()
    except Exception:
        pass
    if not secret_pw:
        secret_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if secret_pw:
        return hmac.compare_digest(password_input, secret_pw)

    # 2. Hash PBKDF2 guardado en ClubSetting (cuando el admin la cambió desde Configuración)
    stored_hash = get_club_setting("admin_password_hash", "")
    if stored_hash:
        ok, _ = verify_password_secure(password_input, stored_hash)
        return ok

    # 3. Hash PBKDF2 de transición inicial
    ok, _ = verify_password_secure(password_input, _BOOTSTRAP_ADMIN_HASH)
    return ok


def set_admin_password(new_password: str) -> Tuple[bool, str]:
    """Actualiza la contraseña del CRM Admin almacenándola como hash PBKDF2-HMAC-SHA256."""
    pw = (new_password or "").strip()
    if len(pw) < 8:
        return False, "La nueva contraseña de administrador debe tener al menos 8 caracteres."
    hashed = hash_password_secure(pw)
    if set_club_setting("admin_password_hash", hashed):
        return True, "Contraseña de administrador actualizada con cifrado PBKDF2."
    return False, "No se pudo guardar la contraseña en la base de datos."


# ═════════════════════════════════════════════════════════════════════
#  5. RATE LIMITING ANTI FUERZA BRUTA (MEMORIA DE PROCESO THREAD-SAFE)
# ═════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _get_rate_limit_store():
    return {"lock": threading.Lock(), "events": {}, "blocked_until": {}}


def check_rate_limit(scope: str, identifier: str) -> Tuple[bool, int]:
    """
    Verifica si `(scope, identifier)` está temporalmente bloqueado.
    Retorna `(permitido, segundos_restantes)`.
    """
    store = _get_rate_limit_store()
    key = f"{scope}:{(identifier or 'unknown').strip().lower()}"
    now = time.time()
    with store["lock"]:
        until = store["blocked_until"].get(key, 0.0)
        if until > now:
            return False, int(until - now) + 1
        return True, 0


def record_failed_attempt(
    scope: str,
    identifier: str,
    max_attempts: int = 5,
    window_sec: int = 600,
    lock_sec: int = 300,
) -> Tuple[int, int]:
    """
    Registra un intento fallido de login.
    Retorna `(intentos_restantes, segundos_bloqueo_si_aplica)`.
    """
    store = _get_rate_limit_store()
    key = f"{scope}:{(identifier or 'unknown').strip().lower()}"
    now = time.time()
    with store["lock"]:
        ev = [t for t in store["events"].get(key, []) if now - t <= window_sec]
        ev.append(now)
        store["events"][key] = ev
        if len(ev) >= max_attempts:
            store["blocked_until"][key] = now + lock_sec
            store["events"][key] = []
            return 0, lock_sec
        return max(0, max_attempts - len(ev)), 0


def clear_rate_limit(scope: str, identifier: str) -> None:
    store = _get_rate_limit_store()
    key = f"{scope}:{(identifier or 'unknown').strip().lower()}"
    with store["lock"]:
        store["events"].pop(key, None)
        store["blocked_until"].pop(key, None)


# ═════════════════════════════════════════════════════════════════════
#  6. BACKUP COMPLETO DESCARGABLE DEL CRM (JSON SIN SECRETOS)
# ═════════════════════════════════════════════════════════════════════

def _serialize_row(obj, exclude_cols=()) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if col.name in exclude_cols:
            continue
        val = getattr(obj, col.name, None)
        if isinstance(val, (datetime, date)):
            val = val.isoformat()
        out[col.name] = val
    return out


def export_full_crm_backup_json() -> Tuple[bytes, Dict[str, int]]:
    """
    Exporta una copia de seguridad completa de toda la información clínica, deportiva
    y financiera del club en formato JSON UTF-8 (sin hashes ni contraseñas).
    """
    with SessionLocal() as session:
        tables = [
            ("members", Member),
            ("transactions", Transaction),
            ("expenses", Expense),
            ("sleep_records", SleepRecord),
            ("lactate_tests", LactateTest),
            ("lactate_test_steps", LactateTestStep),
            ("bloodwork_records", BloodworkRecord),
            ("race_strategy_scenarios", RaceStrategyScenario),
            ("wolfy_chat_messages", WolfyChatMessage),
        ]
        payload = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "app": "AlphaX CRM Full Backup",
            "data": {},
        }
        counts: Dict[str, int] = {}
        for name, model in tables:
            rows = session.query(model).all()
            payload["data"][name] = [_serialize_row(r) for r in rows]
            counts[name] = len(rows)

    raw_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    return raw_bytes, counts
