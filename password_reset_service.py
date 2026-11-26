"""
password_reset_service.py — Servicio de restablecimiento autónomo de contraseña
para la App de Atletas AlphaX Endurance Coaching.

Características de seguridad y reglas del club:
  1. Solo permite restablecer contraseña (y acceder) a deportistas con `Member.active == True`
     en la base de datos del CRM administrativo. Si el atleta está inactivo, bloquea el flujo
     y retorna un aviso claro explicando el motivo.
  2. Método principal (cuando SMTP está configurado en el CRM):
     - Envía un código OTP criptográfico de 6 dígitos al correo electrónico del deportista,
       almacenado únicamente como hash SHA-256 con expiración de 15 minutos y tope de 5 intentos.
  3. Método de respaldo automático (si SMTP aún no está configurado):
     - Verificación multifactor con datos reales del CRM administrativo
       (Correo registrado + Teléfono/WhatsApp registrado + Fecha de nacimiento si existe en el CRM).
  4. Ayuda "¿Olvidaste tu correo?": busca el perfil activo y muestra una pista enmascarada
     (ej. `ca***a@gmail.com`) sin exponer datos completos.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import hmac
import html
import re
import secrets
from typing import Dict, Optional, Tuple

from sqlalchemy import func

from birthday_service import _send_raw_smtp_email, get_smtp_config
from database import AthleteUser, Member, SessionLocal, init_db


RESET_CODE_TTL_MINUTES = 15
RESET_MAX_ATTEMPTS = 5
MIN_PASSWORD_LEN = 6


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _hash_reset_code(email_norm: str, code: str) -> str:
    raw = f"alphax-reset:{email_norm.strip().lower()}:{code.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_phone_digits(phone: str) -> str:
    return re.sub(r"\D+", "", str(phone or ""))


def _phones_match(stored_phone: str, input_phone: str) -> bool:
    """Compara teléfonos tolerando prefijo de país (+57, etc.) sobre los últimos 10 dígitos."""
    s_dig = normalize_phone_digits(stored_phone)
    i_dig = normalize_phone_digits(input_phone)
    if len(s_dig) < 7 or len(i_dig) < 7:
        return False
    if s_dig == i_dig:
        return True
    # Comparar últimos 10 dígitos (celular Colombia/Latinoamérica/USA)
    tail_len = min(10, len(s_dig), len(i_dig))
    return tail_len >= 7 and s_dig[-tail_len:] == i_dig[-tail_len:]


def mask_email(email: str) -> str:
    """Enmascara un correo electrónico (ej. carlos.zuluaga@gmail.com -> ca***a@gmail.com)."""
    e = str(email or "").strip()
    if "@" not in e:
        return "***"
    local, domain = e.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[:2] + "***" + local[-1]
    return f"{masked_local}@{domain}"


def _build_reset_code_email_html(athlete_name: str, code: str, expires_min: int = RESET_CODE_TTL_MINUTES) -> str:
    first_name = html.escape((athlete_name or "Atleta").strip().split()[0].title())
    full_name = html.escape((athlete_name or "Atleta").strip())
    spaced_code = " ".join(list(code))
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"></head>
    <body style="margin:0; padding:0; background-color:#070A12; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#070A12; padding:28px 12px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" style="max-width:540px; background:linear-gradient(145deg,#101726,#090D18); border:1.5px solid #00EEFF; border-radius:18px; overflow:hidden;">
              <tr>
                <td style="background:linear-gradient(135deg,#0B1A2E,#08101E); padding:24px 24px 16px 24px; text-align:center; border-bottom:1px solid rgba(0,238,255,0.25);">
                  <div style="color:#00EEFF; font-size:12px; font-weight:800; letter-spacing:2px; text-transform:uppercase;">
                    ⚡ ALPHAX ENDURANCE COACHING 🐺
                  </div>
                  <h1 style="color:#FFFFFF; font-size:22px; font-weight:900; margin:10px 0 4px 0;">
                    🔑 Código para Restablecer tu Contraseña
                  </h1>
                </td>
              </tr>
              <tr>
                <td style="padding:24px 26px; color:#E2E8F0; font-size:15px; line-height:1.6;">
                  <p style="margin:0 0 12px 0;">Hola <b style="color:#FFFFFF;">{first_name}</b> ({full_name}),</p>
                  <p style="margin:0 0 18px 0;">
                    Recibimos una solicitud para restablecer la contraseña de tu cuenta en la <b style="color:#00EEFF;">App de Atletas AlphaX</b>.
                    Ingresa este código de verificación en la aplicación:
                  </p>
                  <div style="background:rgba(0,238,255,0.10); border:2px solid #00EEFF; border-radius:14px; padding:18px; text-align:center; margin:16px 0;">
                    <div style="color:#94A3B8; font-size:11px; font-weight:800; letter-spacing:1.5px; text-transform:uppercase; margin-bottom:6px;">
                      TU CÓDIGO DE VERIFICACIÓN
                    </div>
                    <div style="color:#FFFFFF; font-size:34px; font-weight:900; letter-spacing:6px; font-family:monospace;">
                      {spaced_code}
                    </div>
                    <div style="color:#FFD700; font-size:12px; font-weight:700; margin-top:8px;">
                      ⏱️ Válido por {expires_min} minutos
                    </div>
                  </div>
                  <p style="margin:14px 0 0 0; color:#94A3B8; font-size:13px;">
                    Si tú no solicitaste este cambio, puedes ignorar este correo de forma segura: tu contraseña actual seguirá intacta.
                  </p>
                </td>
              </tr>
              <tr>
                <td style="background:#060911; padding:14px 24px; text-align:center; border-top:1px solid rgba(255,255,255,0.08);">
                  <div style="color:#00EEFF; font-size:12px; font-weight:800;">AlphaX Endurance Coaching &amp; Physiology 🐺</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


def resolve_account_by_email(session, email_input: str) -> Tuple[str, Optional[AthleteUser], Optional[Member]]:
    """
    Busca una cuenta por correo electrónico (en AthleteUser.email o Member.email) y
    valida el estado de membresía (`Member.active`) en el CRM administrativo.

    Retorna `(status, athlete_user, member)`:
      - `"active"`   -> El deportista existe y está ACTIVO en el CRM.
      - `"inactive"` -> El deportista existe pero está INACTIVO (o fue dado de baja) en el CRM.
      - `"not_found"`-> No existe ninguna cuenta ni perfil CRM con ese correo.
    """
    email_norm = (email_input or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        return "not_found", None, None

    user = (
        session.query(AthleteUser)
        .filter(func.lower(AthleteUser.email) == email_norm)
        .first()
    )
    member = None

    if user:
        member = session.query(Member).filter(Member.name == user.athlete_name).first()
        if not member:
            # Fallback por si el correo coincide directamente en Member.email
            member = (
                session.query(Member)
                .filter(func.lower(Member.email) == email_norm)
                .first()
            )
    else:
        member = (
            session.query(Member)
            .filter(func.lower(Member.email) == email_norm)
            .first()
        )
        if member:
            # Revisar si ya tiene un AthleteUser vinculado por nombre (aunque con otro correo)
            user = session.query(AthleteUser).filter(AthleteUser.athlete_name == member.name).first()

    if not user and not member:
        return "not_found", None, None

    if not member or not bool(member.active):
        return "inactive", user, member

    return "active", user, member


def is_email_recovery_available() -> bool:
    """Retorna True si el servidor SMTP del club está configurado para enviar códigos OTP."""
    try:
        cfg = get_smtp_config()
        return bool(cfg.get("is_configured"))
    except Exception:
        return False


def request_email_reset_code(email_input: str) -> Dict:
    """
    Genera y envía un código OTP de 6 dígitos al correo del deportista ACTIVO.
    Bloquea explícitamente a deportistas inactivos en el CRM.
    """
    init_db()
    email_norm = (email_input or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        return {"ok": False, "status": "invalid_email", "message": "Por favor ingresa un correo electrónico válido."}

    cfg = get_smtp_config()
    if not cfg.get("is_configured"):
        return {
            "ok": False,
            "status": "smtp_not_configured",
            "message": "El envío automático de correos no está configurado todavía. Usa la verificación con tus datos registrados en el CRM.",
        }

    try:
        with SessionLocal() as session:
            status, user, member = resolve_account_by_email(session, email_norm)

            if status == "inactive":
                ath_name = (member.name if member else (user.athlete_name if user else "Deportista"))
                return {
                    "ok": False,
                    "status": "inactive",
                    "athlete_name": ath_name,
                    "message": f"Tu perfil ({ath_name}) se encuentra INACTIVO en AlphaX CRM.",
                }

            if status == "not_found" or not member:
                return {
                    "ok": False,
                    "status": "not_found",
                    "message": (
                        "No encontramos ninguna cuenta ni perfil activo con ese correo electrónico. "
                        "Verifica si usaste otro correo o usa la opción '¿No recuerdas tu correo?' abajo."
                    ),
                }

            # Si el atleta está activo en CRM con ese email pero nunca había creado AthleteUser, crearlo en estado pendiente
            target_email = (user.email.strip().lower() if (user and user.email) else email_norm)
            if not user:
                user = AthleteUser(
                    email=email_norm,
                    password_hash="",
                    athlete_name=member.name,
                )
                session.add(user)
                session.flush()
                target_email = email_norm

            # Control suave de reenvío (evitar spam: mínimo 45 s entre códigos)
            now_utc = datetime.utcnow()
            if user.reset_code_expires:
                created_approx = user.reset_code_expires - timedelta(minutes=RESET_CODE_TTL_MINUTES)
                elapsed_s = (now_utc - created_approx).total_seconds()
                if 0 <= elapsed_s < 45:
                    wait_s = int(45 - elapsed_s)
                    return {
                        "ok": False,
                        "status": "cooldown",
                        "message": f"Ya enviamos un código hace unos segundos. Revisa tu bandeja (o Spam) o espera {wait_s} s para pedir otro.",
                    }

            code = f"{secrets.randbelow(1_000_000):06d}"
            user.reset_code_hash = _hash_reset_code(target_email, code)
            user.reset_code_expires = now_utc + timedelta(minutes=RESET_CODE_TTL_MINUTES)
            user.reset_attempts = 0
            session.commit()

            html_body = _build_reset_code_email_html(member.name, code, RESET_CODE_TTL_MINUTES)
            text_body = (
                f"Hola {member.name},\n\n"
                f"Tu código para restablecer la contraseña en la App de Atletas AlphaX es: {code}\n"
                f"Este código es válido por {RESET_CODE_TTL_MINUTES} minutos.\n\n"
                "Si no solicitaste este cambio, ignora este correo.\n"
                "AlphaX Endurance Coaching"
            )
            sent, smtp_msg = _send_raw_smtp_email(
                cfg,
                target_email,
                f"🔑 Tu código de acceso AlphaX: {code}",
                html_body,
                text_body,
            )
            if not sent:
                return {
                    "ok": False,
                    "status": "smtp_error",
                    "message": f"No pudimos enviar el correo en este momento ({smtp_msg}). Puedes usar la verificación con tus datos del CRM abajo.",
                }

            return {
                "ok": True,
                "status": "code_sent",
                "masked_email": mask_email(target_email),
                "target_email": target_email,
                "athlete_name": member.name,
                "message": f"Enviamos un código de 6 dígitos a **{mask_email(target_email)}**. Revisa también tu carpeta de Spam/Promociones.",
            }
    except Exception as exc:
        return {"ok": False, "status": "error", "message": f"Error procesando la solicitud: {exc}"}


def verify_email_reset_code_and_set_password(
    email_input: str,
    code_input: str,
    new_password: str,
) -> Dict:
    """
    Valida el código OTP de 6 dígitos + membresía activa en el CRM y actualiza la contraseña.
    """
    init_db()
    email_norm = (email_input or "").strip().lower()
    code_clean = re.sub(r"\D+", "", str(code_input or ""))

    if len(code_clean) != 6:
        return {"ok": False, "status": "invalid_code", "message": "El código debe tener exactamente 6 dígitos numéricos."}
    if not new_password or len(new_password.strip()) < MIN_PASSWORD_LEN:
        return {
            "ok": False,
            "status": "weak_password",
            "message": f"La nueva contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.",
        }

    try:
        with SessionLocal() as session:
            status, user, member = resolve_account_by_email(session, email_norm)
            if status == "inactive":
                ath_name = (member.name if member else (user.athlete_name if user else "Deportista"))
                return {
                    "ok": False,
                    "status": "inactive",
                    "athlete_name": ath_name,
                    "message": f"Acceso denegado: {ath_name} se encuentra INACTIVO en AlphaX CRM.",
                }
            if status != "active" or not user or not member:
                return {"ok": False, "status": "not_found", "message": "No existe una solicitud activa para ese correo."}

            if not user.reset_code_hash or not user.reset_code_expires:
                return {"ok": False, "status": "no_code", "message": "Primero debes solicitar un código de verificación."}

            attempts = int(user.reset_attempts or 0)
            if attempts >= RESET_MAX_ATTEMPTS:
                user.reset_code_hash = None
                user.reset_code_expires = None
                session.commit()
                return {
                    "ok": False,
                    "status": "too_many_attempts",
                    "message": "Demasiados intentos fallidos. Solicita un nuevo código de 6 dígitos.",
                }

            if datetime.utcnow() > user.reset_code_expires:
                user.reset_code_hash = None
                user.reset_code_expires = None
                session.commit()
                return {
                    "ok": False,
                    "status": "expired",
                    "message": "El código ha expirado (validez de 15 minutos). Solicita uno nuevo.",
                }

            expected_hash = _hash_reset_code(user.email, code_clean)
            if not hmac.compare_digest(expected_hash, str(user.reset_code_hash)):
                user.reset_attempts = attempts + 1
                session.commit()
                left = max(0, RESET_MAX_ATTEMPTS - user.reset_attempts)
                return {
                    "ok": False,
                    "status": "wrong_code",
                    "message": f"Código incorrecto. Te quedan {left} intento(s).",
                }

            # Código válido y deportista activo -> actualizar contraseña y limpiar código
            user.password_hash = hash_password(new_password)
            user.reset_code_hash = None
            user.reset_code_expires = None
            user.reset_attempts = 0
            if not getattr(member, "email", None):
                member.email = user.email
            session.commit()

            return {
                "ok": True,
                "status": "password_updated",
                "athlete_name": member.name,
                "member_id": member.id,
                "email": user.email,
                "message": f"✅ Contraseña restablecida correctamente para **{member.name}**. Ya puedes iniciar sesión.",
            }
    except Exception as exc:
        return {"ok": False, "status": "error", "message": f"Error al restablecer la contraseña: {exc}"}


def verify_crm_identity_and_set_password(
    email_input: str,
    phone_input: str,
    birth_date_input: Optional[date],
    new_password: str,
) -> Dict:
    """
    Respaldo autónomo de restablecimiento de contraseña mediante verificación de datos reales
    del CRM administrativo (solo deportistas ACTIVOS):
      - Correo registrado (coincidencia exacta con AthleteUser.email o Member.email)
      - Teléfono / WhatsApp registrado en `Member.phone` (obligatorio)
      - Fecha de nacimiento `Member.birth_date` (obligatoria si está registrada en el CRM)
    """
    init_db()
    email_norm = (email_input or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        return {"ok": False, "status": "invalid_email", "message": "Ingresa un correo electrónico válido."}
    if not phone_input or len(normalize_phone_digits(phone_input)) < 7:
        return {"ok": False, "status": "invalid_phone", "message": "Ingresa el número de teléfono/WhatsApp registrado en AlphaX."}
    if not new_password or len(new_password.strip()) < MIN_PASSWORD_LEN:
        return {
            "ok": False,
            "status": "weak_password",
            "message": f"La nueva contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.",
        }

    try:
        with SessionLocal() as session:
            status, user, member = resolve_account_by_email(session, email_norm)
            if status == "inactive":
                ath_name = (member.name if member else (user.athlete_name if user else "Deportista"))
                return {
                    "ok": False,
                    "status": "inactive",
                    "athlete_name": ath_name,
                    "message": f"Acceso denegado: {ath_name} se encuentra INACTIVO en AlphaX CRM.",
                }
            if status != "active" or not member:
                return {
                    "ok": False,
                    "status": "not_found",
                    "message": "No encontramos un deportista activo con ese correo electrónico.",
                }

            # Verificar teléfono registrado en CRM
            stored_phone = getattr(member, "phone", None) or ""
            if not stored_phone or len(normalize_phone_digits(stored_phone)) < 7:
                return {
                    "ok": False,
                    "status": "missing_crm_data",
                    "message": (
                        "Tu perfil aún no tiene un número de teléfono registrado en el CRM para validar tu identidad. "
                        "Usa el código por correo electrónico o pide a tu entrenador que actualice tu ficha."
                    ),
                }
            if not _phones_match(stored_phone, phone_input):
                return {
                    "ok": False,
                    "status": "mismatch",
                    "message": "Los datos ingresados no coinciden con tu ficha registrada en AlphaX CRM.",
                }

            # Si el CRM tiene fecha de nacimiento registrada, exigir coincidencia exacta
            stored_bdate = getattr(member, "birth_date", None)
            if stored_bdate is not None:
                if birth_date_input is None or birth_date_input != stored_bdate:
                    return {
                        "ok": False,
                        "status": "mismatch",
                        "message": "La fecha de nacimiento no coincide con la registrada en tu ficha de AlphaX CRM.",
                    }

            # Validación exitosa -> actualizar o crear AthleteUser
            if not user:
                user = AthleteUser(
                    email=email_norm,
                    password_hash=hash_password(new_password),
                    athlete_name=member.name,
                )
                session.add(user)
            else:
                user.password_hash = hash_password(new_password)
                user.reset_code_hash = None
                user.reset_code_expires = None
                user.reset_attempts = 0
            if not getattr(member, "email", None):
                member.email = user.email
            session.commit()

            return {
                "ok": True,
                "status": "password_updated",
                "athlete_name": member.name,
                "member_id": member.id,
                "email": user.email,
                "message": f"✅ Identidad verificada. Contraseña actualizada para **{member.name}**.",
            }
    except Exception as exc:
        return {"ok": False, "status": "error", "message": f"Error verificando identidad: {exc}"}


def lookup_account_hint_by_name(name_query: str, match_fn) -> Dict:
    """
    Ayuda al deportista que olvidó con qué correo se registró:
      - Si coincide con un deportista ACTIVO -> retorna pista de correo enmascarada (`ca***a@gmail.com`).
      - Si coincide con un deportista INACTIVO -> retorna `status='inactive'` para avisarle por qué no puede entrar.
    """
    init_db()
    q = (name_query or "").strip()
    if len(q) < 3:
        return {"status": "too_short", "matches": []}

    try:
        with SessionLocal() as session:
            all_members = session.query(Member).all()
            matches = match_fn(q, all_members)
            if not matches:
                return {"status": "not_found", "matches": []}

            top = matches[0]
            if not bool(top.active):
                return {
                    "status": "inactive",
                    "athlete_name": top.name,
                }

            user = session.query(AthleteUser).filter(AthleteUser.athlete_name == top.name).first()
            email_found = (user.email if (user and user.email) else getattr(top, "email", None)) or ""
            return {
                "status": "active",
                "athlete_name": top.name,
                "has_account": bool(user),
                "masked_email": mask_email(email_found) if email_found and "@" in email_found else "",
            }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
