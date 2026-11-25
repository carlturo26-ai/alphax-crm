"""
birthday_service.py — Servicio de felicitaciones de cumpleaños por correo electrónico
para los deportistas del Club AlphaX Endurance Coaching.

Soporta configuración SMTP centralizada en ClubSetting (Neon Postgres / SQLite) o st.secrets:
  - smtp_host (default: smtp.gmail.com)
  - smtp_port (default: 587)
  - smtp_user (correo remitente)
  - smtp_password (Contraseña de Aplicación / App Password)
  - smtp_from_name (default: AlphaX Endurance Coaching)
  - birthday_auto_enabled ("true" / "false")
  - birthday_custom_msg (mensaje adicional del club)
"""

from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import os
import smtplib
import ssl
import streamlit as st

from database import AthleteUser, Member, SessionLocal, get_club_setting, init_db, set_club_setting


def get_smtp_config() -> dict:
    """Obtiene la configuración SMTP desde ClubSetting, st.secrets o variables de entorno."""
    def _pick(club_key: str, secret_key: str, default: str = "") -> str:
        val = get_club_setting(club_key, "")
        if val:
            return val.strip()
        try:
            if secret_key in st.secrets and st.secrets[secret_key]:
                return str(st.secrets[secret_key]).strip()
        except Exception:
            pass
        env_v = os.environ.get(secret_key, "").strip()
        return env_v if env_v else default

    host = _pick("smtp_host", "SMTP_HOST", "smtp.gmail.com")
    port_raw = _pick("smtp_port", "SMTP_PORT", "587")
    try:
        port = int(port_raw)
    except Exception:
        port = 587
    user = _pick("smtp_user", "SMTP_USER", "")
    password = _pick("smtp_password", "SMTP_PASSWORD", "")
    from_name = _pick("smtp_from_name", "SMTP_FROM_NAME", "AlphaX Endurance Coaching")
    auto_enabled = _pick("birthday_auto_enabled", "BIRTHDAY_AUTO_ENABLED", "true").lower() in ("true", "1", "yes", "si")
    custom_msg = get_club_setting(
        "birthday_custom_msg",
        "Que este nuevo año de vida venga cargado de salud, kilómetros increíbles, nuevos récords personales y metas cumplidas junto a la manada AlphaX.",
    )
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_name": from_name,
        "auto_enabled": auto_enabled,
        "custom_msg": custom_msg,
        "is_configured": bool(host and user and password),
    }


def save_smtp_config(
    host: str,
    port: int,
    user: str,
    password: str,
    from_name: str,
    auto_enabled: bool,
    custom_msg: str,
) -> bool:
    """Guarda la configuración SMTP y de cumpleaños en la base de datos central del club."""
    ok = True
    ok &= set_club_setting("smtp_host", (host or "smtp.gmail.com").strip())
    ok &= set_club_setting("smtp_port", str(int(port or 587)))
    ok &= set_club_setting("smtp_user", (user or "").strip())
    if password and password.strip():
        ok &= set_club_setting("smtp_password", password.strip())
    ok &= set_club_setting("smtp_from_name", (from_name or "AlphaX Endurance Coaching").strip())
    ok &= set_club_setting("birthday_auto_enabled", "true" if auto_enabled else "false")
    ok &= set_club_setting("birthday_custom_msg", (custom_msg or "").strip())
    return ok


def _build_birthday_email_html(athlete_name: str, age_turning: int = None, custom_msg: str = "") -> str:
    """Construye una plantilla HTML premium estilo AlphaX para la felicitación de cumpleaños."""
    first_name = html.escape((athlete_name or "Atleta").strip().split()[0].title())
    full_name = html.escape((athlete_name or "Atleta").strip())
    age_badge = (
        f"<div style='display:inline-block; background:rgba(0,238,255,0.14); border:1px solid #00EEFF; "
        f"color:#00EEFF; font-size:13px; font-weight:800; padding:5px 14px; border-radius:20px; margin-top:8px;'>"
        f"🎂 ¡Celebrando tus {age_turning} años!</div>"
        if age_turning
        else ""
    )
    msg_clean = html.escape(
        custom_msg
        or "Que este nuevo año de vida venga cargado de salud, kilómetros increíbles, nuevos récords personales y metas cumplidas junto a la manada AlphaX."
    )
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"></head>
    <body style="margin:0; padding:0; background-color:#070A12; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#070A12; padding:28px 12px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px; background:linear-gradient(145deg,#101726,#090D18); border:1.5px solid #00EEFF; border-radius:18px; overflow:hidden;">
              <tr>
                <td style="background:linear-gradient(135deg,#0B1A2E,#08101E); padding:26px 24px 18px 24px; text-align:center; border-bottom:1px solid rgba(0,238,255,0.25);">
                  <div style="color:#00EEFF; font-size:12px; font-weight:800; letter-spacing:2px; text-transform:uppercase;">
                    ⚡ ALPHAX ENDURANCE COACHING 🐺
                  </div>
                  <h1 style="color:#FFFFFF; font-size:26px; font-weight:900; margin:10px 0 4px 0;">
                    🎉 ¡Feliz Cumpleaños, {first_name}! 🎉
                  </h1>
                  {age_badge}
                </td>
              </tr>
              <tr>
                <td style="padding:24px 26px; color:#E2E8F0; font-size:15px; line-height:1.65;">
                  <p style="margin:0 0 14px 0;">Hola <b style="color:#FFFFFF;">{full_name}</b>,</p>
                  <p style="margin:0 0 16px 0;">
                    Desde todo el equipo de entrenadores y compañeros del <b style="color:#00EEFF;">Club AlphaX</b> queremos enviarte un abrazo enorme en tu día especial.
                  </p>
                  <div style="background:rgba(0,238,255,0.07); border-left:4px solid #00EEFF; border-radius:8px; padding:14px 16px; margin:16px 0; color:#F8FAFC;">
                    {msg_clean}
                  </div>
                  <p style="margin:14px 0 0 0; color:#CBD5E1;">
                    Gracias por tu disciplina, constancia y energía dentro del equipo. ¡Hoy celebra en grande (y que no falte el pastel para recargar glucógeno 🍰⚡)!
                  </p>
                </td>
              </tr>
              <tr>
                <td style="background:#060911; padding:16px 24px; text-align:center; border-top:1px solid rgba(255,255,255,0.08);">
                  <div style="color:#00EEFF; font-size:13px; font-weight:800;">Con aprecio, tu equipo AlphaX 🐺</div>
                  <div style="color:#64748B; font-size:11px; margin-top:4px;">AlphaX Endurance Coaching &amp; Physiology</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


def _send_raw_smtp_email(cfg: dict, to_email: str, subject: str, html_body: str, text_body: str) -> tuple:
    """Envía un correo multipart HTML + texto plano usando la configuración SMTP dada."""
    if not cfg.get("is_configured"):
        return False, "SMTP no configurado. Ingresa tu correo y Contraseña de Aplicación en Configuración."
    if not to_email or "@" not in to_email:
        return False, "Correo electrónico del destinatario inválido."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['user']}>"
    msg["To"] = to_email.strip()
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        port = int(cfg["port"])
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, timeout=15, context=context) as server:
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["user"], [to_email.strip()], msg.as_string())
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["user"], [to_email.strip()], msg.as_string())
        return True, "Correo enviado exitosamente."
    except Exception as err:
        return False, f"Error SMTP: {err}"


def send_test_email(to_email: str) -> tuple:
    """Envía un correo de prueba con la plantilla de cumpleaños AlphaX."""
    cfg = get_smtp_config()
    html_b = _build_birthday_email_html("Atleta de Prueba", age_turning=30, custom_msg=cfg["custom_msg"])
    txt_b = "¡Feliz Cumpleaños de parte del Club AlphaX Endurance Coaching! (Correo de prueba SMTP)."
    return _send_raw_smtp_email(cfg, to_email, "🎂 [Prueba] ¡Feliz Cumpleaños de parte de AlphaX! 🐺", html_b, txt_b)


def _resolve_member_email(session, member: Member) -> str:
    """Obtiene el correo de Member.email o hace fallback al correo de su cuenta AthleteUser."""
    if getattr(member, "email", None) and "@" in str(member.email):
        return str(member.email).strip()
    try:
        u = session.query(AthleteUser).filter(AthleteUser.athlete_name == member.name).first()
        if u and u.email and "@" in u.email:
            return u.email.strip()
    except Exception:
        pass
    return ""


def _days_until_next_birthday(bdate: date, today: date = None) -> tuple:
    """Retorna (dias_faltantes, edad_que_cumple) para una fecha de nacimiento."""
    today = today or date.today()
    try:
        next_bd = date(today.year, bdate.month, bdate.day)
    except ValueError:
        # 29 de febrero en año no bisiesto -> 28 de febrero
        next_bd = date(today.year, 2, 28)
    if next_bd < today:
        try:
            next_bd = date(today.year + 1, bdate.month, bdate.day)
        except ValueError:
            next_bd = date(today.year + 1, 2, 28)
    days_left = (next_bd - today).days
    age_turning = next_bd.year - bdate.year
    return days_left, age_turning


def get_upcoming_birthdays(days_ahead: int = 30) -> list:
    """Retorna lista ordenada de cumpleañeros activos de hoy y los próximos `days_ahead` días."""
    init_db()
    today = date.today()
    out = []
    try:
        with SessionLocal() as session:
            members = (
                session.query(Member)
                .filter(Member.active == True, Member.birth_date.isnot(None))
                .all()
            )
            for m in members:
                if not m.birth_date:
                    continue
                days_left, age_turning = _days_until_next_birthday(m.birth_date, today)
                if 0 <= days_left <= days_ahead:
                    email_addr = _resolve_member_email(session, m)
                    sent_this_year = (getattr(m, "birthday_email_last_year", None) == today.year)
                    out.append({
                        "member_id": m.id,
                        "name": m.name,
                        "group": m.group or "AlphaX",
                        "birth_date": m.birth_date,
                        "days_until": days_left,
                        "is_today": (days_left == 0),
                        "age_turning": age_turning,
                        "email": email_addr,
                        "phone": m.phone or "",
                        "sent_this_year": sent_this_year,
                    })
    except Exception:
        return []
    out.sort(key=lambda x: (x["days_until"], x["name"]))
    return out


def send_birthday_email_to_member(member_id: int, force: bool = False) -> tuple:
    """Envía la felicitación de cumpleaños a un deportista específico y registra el envío del año."""
    init_db()
    cfg = get_smtp_config()
    if not cfg["is_configured"]:
        return False, "SMTP no configurado aún en Configuración."

    today = date.today()
    with SessionLocal() as session:
        m = session.query(Member).filter(Member.id == member_id).first()
        if not m:
            return False, "Deportista no encontrado."
        if not force and getattr(m, "birthday_email_last_year", None) == today.year:
            return True, f"Ya se había enviado la felicitación de {today.year} a {m.name}."
        email_addr = _resolve_member_email(session, m)
        if not email_addr:
            return False, f"{m.name} no tiene correo electrónico registrado."

        age_turning = (today.year - m.birth_date.year) if m.birth_date else None
        first_name = (m.name or "Atleta").strip().split()[0].title()
        subject = f"🎂 ¡Feliz Cumpleaños, {first_name}! — Club AlphaX 🐺"
        html_b = _build_birthday_email_html(m.name, age_turning=age_turning, custom_msg=cfg["custom_msg"])
        txt_b = (
            f"¡Feliz Cumpleaños, {m.name}!\n\n"
            f"{cfg['custom_msg']}\n\n"
            f"Un abrazo enorme de parte de todo el equipo de AlphaX Endurance Coaching 🐺⚡"
        )
        ok, msg = _send_raw_smtp_email(cfg, email_addr, subject, html_b, txt_b)
        if ok:
            m.birthday_email_last_year = today.year
            if not m.email:
                m.email = email_addr
            session.commit()
        return ok, msg


def process_todays_birthdays_auto() -> list:
    """
    Revisa silenciosamente (máx. 1 vez por día y sesión) si hoy cumple años algún atleta activo
    con correo registrado y envía automáticamente su felicitación si SMTP está habilitado.
    Retorna lista de nombres a los que se les envió el correo en esta ejecución.
    """
    today_key = f"_alphax_bday_checked_{date.today().isoformat()}"
    if st.session_state.get(today_key):
        return []
    st.session_state[today_key] = True

    cfg = get_smtp_config()
    if not cfg["is_configured"] or not cfg["auto_enabled"]:
        return []

    sent_names = []
    try:
        todays = [b for b in get_upcoming_birthdays(days_ahead=0) if b["is_today"] and not b["sent_this_year"] and b["email"]]
        for b in todays:
            ok, _ = send_birthday_email_to_member(b["member_id"], force=False)
            if ok:
                sent_names.append(b["name"])
    except Exception:
        pass
    return sent_names
