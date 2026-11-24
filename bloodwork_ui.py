"""
bloodwork_ui.py — Componentes de interfaz amigables y compartidos para la carga,
revisión, protección anti-duplicados y gestión de exámenes de sangre en AlphaX CRM
y Portal de Atletas.
"""

from datetime import datetime
import importlib
import streamlit as st

from bloodwork_constants import ALL_MARKER_KEYS, classify_bloodwork_value
from bloodwork_service import (
    clean_numeric_marker,
    count_record_markers,
    extract_marker_dict,
    find_duplicate_groups,
    find_potential_duplicates,
    merge_duplicate_records,
    save_or_merge_bloodwork,
)


MARKER_SECTIONS = [
    (
        "🔴 1. Serie Roja y Transporte de Oxígeno",
        [
            ("hemoglobin", 0.1),
            ("hematocrit", 0.1),
            ("rbc", 0.01),
            ("vcm", 0.1),
            ("chcm", 0.1),
            ("ferritin", 1.0),
        ],
    ),
    (
        "⚡ 2. Daño Muscular y Vitaminas",
        [
            ("ck", 1.0),
            ("vitamin_b12", 1.0),
            ("folic_acid", 0.1),
        ],
    ),
    (
        "🫀 3. Perfil Lipídico Cardiovascular",
        [
            ("total_cholesterol", 1.0),
            ("hdl", 1.0),
            ("ldl", 1.0),
            ("triglycerides", 1.0),
        ],
    ),
    (
        "🍯 4. Glucemia, HbA1c e Inflamación",
        [
            ("glucose", 1.0),
            ("hba1c", 0.1),
            ("pcr_us", 0.01),
        ],
    ),
]


def _load_parser():
    import hemograma_parser
    try:
        importlib.reload(hemograma_parser)
    except Exception:
        pass
    return hemograma_parser


def _fmt_val(v) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 0.005:
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _short_filename(fname: str, max_len: int = 38) -> str:
    if not fname:
        return "Ingreso manual"
    if len(fname) <= max_len:
        return fname
    return fname[: max_len - 3] + "..."


def _render_html(html_str: str):
    if hasattr(st, "html"):
        st.html(html_str)
    else:
        st.markdown(html_str, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  1. BANNER DE UNIFICACIÓN DE DUPLICADOS EXISTENTES
# ═══════════════════════════════════════════════════════════════════

def render_duplicate_cleanup_banner(
    records: list,
    member_id: int,
    SessionLocal,
    BloodworkRecord,
    key_prefix: str = "ath",
):
    """
    Si existen 2 o más exámenes en la misma fecha para el deportista, muestra
    un aviso amigable con botón de 1 clic para unificarlos sin perder datos.
    """
    banner_flash_key = f"{key_prefix}_bw_flash"
    if st.session_state.get(banner_flash_key):
        st.success(st.session_state.pop(banner_flash_key))

    dup_groups = find_duplicate_groups(records)
    if not dup_groups:
        return

    total_extra = sum(g["count"] - 1 for g in dup_groups)
    _render_html(
        f"""
        <div style="background: linear-gradient(135deg, rgba(255, 170, 0, 0.16), rgba(20, 22, 34, 0.96));
                    border: 1.5px solid #FFAA00; border-radius: 12px; padding: 12px 15px; margin: 8px 0 14px 0;">
            <div style="color: #FFCC44; font-weight: 800; font-size: 0.92rem;">
                🧹 Detectamos {total_extra} registro(s) duplicado(s) con la misma fecha
            </div>
            <div style="color: #E2E8F0; font-size: 0.82rem; margin-top: 4px; line-height: 1.4;">
                Puedes unificarlos con un clic: conservaremos <b>todos los biomarcadores</b> en un solo examen limpio y eliminaremos las copias repetidas.
            </div>
        </div>
        """
    )

    for grp in dup_groups:
        dt_str = grp["date"].strftime("%d/%m/%Y")
        rec_ids = [r.id for r in grp["records"]]
        counts_txt = ", ".join(f"{count_record_markers(r)}/16" for r in grp["records"])
        btn_label = (
            f"🔀 Unificar {grp['count']} exámenes del {dt_str} "
            f"({counts_txt}) → 1 examen ({grp['merged_count']}/16 marcadores)"
        )
        if st.button(btn_label, key=f"{key_prefix}_unify_{grp['date'].isoformat()}", use_container_width=True):
            try:
                with SessionLocal() as session:
                    _, removed = merge_duplicate_records(session, BloodworkRecord, member_id, rec_ids)
                st.session_state[f"{key_prefix}_bw_flash"] = (
                    f"✅ Se unificaron los exámenes del {dt_str} en un solo registro limpio ({removed} duplicado(s) eliminado(s))."
                )
                st.toast("Duplicados unificados correctamente", icon="🧹")
                st.rerun()
            except Exception as err:
                st.error(f"No se pudieron unificar los duplicados: {err}")


# ═══════════════════════════════════════════════════════════════════
#  2. ASISTENTE GUIADO DE CARGA EN 3 PASOS (CON ANTI-DUPLICADOS)
# ═══════════════════════════════════════════════════════════════════

def render_bloodwork_upload_wizard(
    member_id: int,
    gender: str,
    ranges: dict,
    SessionLocal,
    BloodworkRecord,
    key_prefix: str = "ath",
    is_coach: bool = False,
    athlete_name: str = "",
):
    """
    Flujo guiado de 3 pasos para subir exámenes de sangre sin errores ni duplicados:
      Paso 1: Subir 1 o varios PDFs / fotos (con detección automática de PDF con clave) o ingreso manual.
      Paso 2: Revisar y corregir fecha y biomarcadores con vista previa clínica antes de guardar.
      Paso 3: Protección inteligente anti-duplicados (Combinar / Reemplazar / Guardar aparte / Cancelar).
    """
    ver_key = f"{key_prefix}_bw_uploader_ver"
    draft_key = f"{key_prefix}_bw_draft"
    draft_seq_key = f"{key_prefix}_bw_draft_seq"
    pending_key = f"{key_prefix}_bw_pending_save"
    flash_key = f"{key_prefix}_bw_flash"

    if ver_key not in st.session_state:
        st.session_state[ver_key] = 0
    if draft_seq_key not in st.session_state:
        st.session_state[draft_seq_key] = 0

    # Mostrar confirmación persistente tras st.rerun()
    if st.session_state.get(flash_key):
        st.success(st.session_state.pop(flash_key))

    gender_badge = "👩 Mujer" if str(gender).strip().lower() == "mujer" else "👨 Hombre"
    draft = st.session_state.get(draft_key)
    pending = st.session_state.get(pending_key)

    def _reset_wizard():
        st.session_state.pop(draft_key, None)
        st.session_state.pop(pending_key, None)
        st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1

    # ── INDICADOR VISUAL DE PASOS ─────────────────────────────────
    step_now = 3 if pending else (2 if draft else 1)
    steps_meta = [
        (1, "1. Subir Archivo(s)"),
        (2, "2. Revisar Datos"),
        (3, "3. Confirmar"),
    ]
    pills_html = '<div style="display:flex; gap:6px; justify-content:center; margin:4px 0 14px 0; flex-wrap:wrap;">'
    for num, label in steps_meta:
        if num == step_now:
            style = "background:rgba(0,238,255,0.18); color:#00EEFF; border:1.5px solid #00EEFF; font-weight:800;"
        elif num < step_now:
            style = "background:rgba(0,255,136,0.14); color:#00FF88; border:1px solid rgba(0,255,136,0.45); font-weight:700;"
        else:
            style = "background:rgba(255,255,255,0.04); color:#7F8EA3; border:1px solid rgba(255,255,255,0.12); font-weight:600;"
        pills_html += f'<span style="padding:4px 10px; border-radius:20px; font-size:0.75rem; {style}">{label}</span>'
    pills_html += "</div>"
    _render_html(pills_html)

    # ═══════════════════════════════════════════════════════════════
    #  PASO 3: RESOLUCIÓN DE DUPLICADOS / EXAMEN EN MISMA FECHA
    # ═══════════════════════════════════════════════════════════════
    if pending:
        top_match = pending["matches"][0]
        rec_id = top_match["record_id"]
        rec_date_str = top_match["date"].strftime("%d/%m/%Y") if top_match.get("date") else "—"
        new_date_str = pending["date"].strftime("%d/%m/%Y")
        reason = top_match["reason"]

        if top_match["is_exact_duplicate"]:
            headline = f"⚠️ Este examen ya estaba cargado ({rec_date_str})"
            subtitle = (
                "Todos los biomarcadores que estás intentando guardar coinciden exactamente con el examen ya registrado. "
                "Para evitar duplicados, puedes cancelar sin perder nada."
            )
            border_c = "#FF5555"
        elif reason == "misma_fecha":
            headline = f"🔎 Ya existe un examen guardado del {rec_date_str}"
            subtitle = (
                f"El registro existente tiene <b>{top_match['existing_count']}/16</b> biomarcadores. "
                "Si estás subiendo otra hoja o complemento del mismo laboratorio, elige <b>Combinar y completar</b> para unir todo en un solo examen."
            )
            border_c = "#FFAA00"
        elif reason == "valores_identicos":
            headline = f"⚠️ Resultados muy similares al examen del {rec_date_str}"
            subtitle = (
                f"Coinciden <b>{len(top_match['matching'])} biomarcadores</b> exactos con tu examen del {rec_date_str} "
                f"(aunque la fecha seleccionada ahora es {new_date_str}). Revisa si se trata del mismo examen."
            )
            border_c = "#FFAA00"
        elif reason == "archivo_repetido":
            headline = f"⚠️ Archivo con el mismo nombre ya cargado ({rec_date_str})"
            subtitle = (
                f"Anteriormente ya cargaste <code>{top_match.get('pdf_filename') or 'este archivo'}</code> el {rec_date_str}."
            )
            border_c = "#FFAA00"
        else:
            headline = f"ℹ️ Tienes un examen muy cercano ({rec_date_str})"
            subtitle = (
                f"Hay un registro a {top_match['day_diff']} día(s) con <b>{top_match['existing_count']}/16</b> marcadores. "
                "Si corresponden a la misma toma de laboratorio (ej. una parte entregada al día siguiente), puedes combinarlos."
            )
            border_c = "#00EEFF"

        _render_html(
            f"""
            <div style="background: rgba(18, 22, 34, 0.96); border: 2px solid {border_c};
                        border-radius: 14px; padding: 15px 16px; margin-bottom: 14px;">
                <div style="color: {border_c}; font-weight: 900; font-size: 1rem;">{headline}</div>
                <div style="color: #E2E8F0; font-size: 0.86rem; margin-top: 6px; line-height: 1.45;">{subtitle}</div>
            </div>
            """
        )

        # Resumen de diferencias / complementos
        comp_keys = top_match.get("complementary", [])
        match_keys = top_match.get("matching", [])
        conflict_list = top_match.get("conflicts", [])

        if comp_keys:
            comp_names = ", ".join(ranges[k]["name"] for k in comp_keys if k in ranges)
            st.success(f"➕ **Aporta {len(comp_keys)} biomarcador(es) nuevo(s) que faltaban:** {comp_names}")
        if match_keys:
            st.info(f"✅ **{len(match_keys)} biomarcador(es) coinciden exactamente** con el examen guardado.")
        if conflict_list:
            diff_lines = []
            for k, old_v, new_v in conflict_list:
                m_name = ranges[k]["name"] if k in ranges else k
                unit = ranges[k]["unit"] if k in ranges else ""
                diff_lines.append(f"- **{m_name}**: guardado `{_fmt_val(old_v)}` → nuevo `{_fmt_val(new_v)}` {unit}")
            st.warning("⚠️ **Valores diferentes encontrados:**\n" + "\n".join(diff_lines))

        st.markdown("##### ¿Cómo deseas proceder?")
        c_act1, c_act2 = st.columns(2)

        with c_act1:
            if not top_match["is_exact_duplicate"]:
                if st.button(
                    "🔀 Combinar y completar examen existente (Recomendado)",
                    type="primary",
                    use_container_width=True,
                    key=f"{key_prefix}_dup_merge_btn",
                ):
                    try:
                        with SessionLocal() as session:
                            _, msg = save_or_merge_bloodwork(
                                session,
                                BloodworkRecord,
                                member_id=member_id,
                                exam_date=pending["date"],
                                values=pending["values"],
                                pdf_filename=pending["filename"],
                                notes=pending["notes"],
                                mode="merge_fill",
                                target_record_id=rec_id,
                                overwrite_conflicts=True,
                            )
                        _reset_wizard()
                        st.session_state[flash_key] = f"🎉 {msg}"
                        st.toast("Examen combinado sin duplicados", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al combinar examen: {e}")

                if st.button(
                    "🔄 Reemplazar examen existente por completo",
                    use_container_width=True,
                    key=f"{key_prefix}_dup_replace_btn",
                ):
                    try:
                        with SessionLocal() as session:
                            _, msg = save_or_merge_bloodwork(
                                session,
                                BloodworkRecord,
                                member_id=member_id,
                                exam_date=pending["date"],
                                values=pending["values"],
                                pdf_filename=pending["filename"],
                                notes=pending["notes"],
                                mode="replace",
                                target_record_id=rec_id,
                            )
                        _reset_wizard()
                        st.session_state[flash_key] = f"🎉 {msg}"
                        st.toast("Examen actualizado", icon="🔄")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al reemplazar examen: {e}")
            else:
                if st.button(
                    "❌ Cancelar (ya estaba guardado)",
                    type="primary",
                    use_container_width=True,
                    key=f"{key_prefix}_dup_cancel_exact_btn",
                ):
                    _reset_wizard()
                    st.session_state[flash_key] = "ℹ️ Operación cancelada. No se creó ningún duplicado."
                    st.rerun()

        with c_act2:
            if st.button(
                "⬅️ Volver a revisar / cambiar fecha",
                use_container_width=True,
                key=f"{key_prefix}_dup_back_btn",
            ):
                st.session_state.pop(pending_key, None)
                st.rerun()

            if st.button(
                "➕ Guardar de todos modos como examen aparte",
                use_container_width=True,
                key=f"{key_prefix}_dup_create_anyway_btn",
            ):
                try:
                    with SessionLocal() as session:
                        _, msg = save_or_merge_bloodwork(
                            session,
                            BloodworkRecord,
                            member_id=member_id,
                            exam_date=pending["date"],
                            values=pending["values"],
                            pdf_filename=pending["filename"],
                            notes=pending["notes"],
                            mode="create",
                        )
                    _reset_wizard()
                    st.session_state[flash_key] = f"🎉 {msg}"
                    st.toast("Examen guardado", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")

            if not top_match["is_exact_duplicate"]:
                if st.button(
                    "❌ Descartar y cancelar",
                    use_container_width=True,
                    key=f"{key_prefix}_dup_discard_btn",
                ):
                    _reset_wizard()
                    st.rerun()
        return

    # ═══════════════════════════════════════════════════════════════
    #  PASO 1: SUBIR ARCHIVO(S) O INGRESO MANUAL
    # ═══════════════════════════════════════════════════════════════
    if not draft:
        _render_html(
            f"""
            <div style="background: rgba(0, 238, 255, 0.06); border: 1px solid rgba(0, 238, 255, 0.28);
                        border-radius: 12px; padding: 12px 14px; margin-bottom: 12px;">
                <div style="color: #00EEFF; font-weight: 800; font-size: 0.9rem;">
                    📤 Sube tu examen de sangre (PDF o Fotos) · Perfil: {gender_badge}
                </div>
                <div style="color: #CBD5E1; font-size: 0.81rem; margin-top: 4px; line-height: 1.45;">
                    • Puedes seleccionar <b>1 o varios archivos juntos</b> (ej. varios PDFs o 2-3 fotos del mismo examen) y los unimos automáticamente.<br>
                    • <b>Nada se guarda sin tu confirmación:</b> en el siguiente paso revisarás los valores y evitaremos duplicados.
                </div>
            </div>
            """
        )

        uploaded_files = st.file_uploader(
            "Selecciona uno o varios archivos (PDF, JPG, PNG, WEBP):",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"{key_prefix}_bw_files_v{st.session_state[ver_key]}",
        )

        # Detección proactiva de PDFs protegidos con contraseña
        passwords_map = {}
        if uploaded_files:
            parser_mod = _load_parser()
            locked_files = []
            for f in uploaded_files:
                if f.name.lower().endswith(".pdf"):
                    try:
                        if parser_mod.is_pdf_encrypted(f.getvalue()):
                            locked_files.append(f.name)
                    except Exception:
                        pass

            if locked_files:
                st.warning(
                    f"🔒 **PDF protegido con contraseña detectado:** `{', '.join(locked_files)}`. "
                    "Normalmente es tu número de documento/cédula."
                )
                if len(locked_files) == 1:
                    pwd_val = st.text_input(
                        f"Clave para {locked_files[0]}:",
                        type="password",
                        key=f"{key_prefix}_pwd_single_{st.session_state[ver_key]}",
                    )
                    if pwd_val:
                        passwords_map[locked_files[0]] = pwd_val
                else:
                    common_pwd = st.text_input(
                        "Clave común para los PDFs protegidos:",
                        type="password",
                        key=f"{key_prefix}_pwd_common_{st.session_state[ver_key]}",
                    )
                    for lf in locked_files:
                        if common_pwd:
                            passwords_map[lf] = common_pwd

            if st.button(
                f"🔍 Leer y Revisar Examen ({len(uploaded_files)} archivo{'s' if len(uploaded_files) > 1 else ''})",
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_btn_extract_bw",
            ):
                with st.spinner("🤖 Leyendo y analizando tus biomarcadores clínicamente..."):
                    try:
                        parser_mod = _load_parser()
                        files_payload = [(f.getvalue(), f.name) for f in uploaded_files]
                        parsed = parser_mod.process_multiple_files(
                            files_payload,
                            passwords=passwords_map if passwords_map else None,
                            use_ai=True,
                        )

                        # Si todos los archivos fallaron por contraseña/error, mostrar error claro sin avanzar
                        if parsed.get("errors") and not parsed.get("filenames"):
                            for err_msg in parsed["errors"]:
                                st.error(f"⚠️ {err_msg}")
                        else:
                            parsed_date = datetime.now().date()
                            date_detected = False
                            if parsed.get("date"):
                                try:
                                    parsed_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
                                    date_detected = True
                                except Exception:
                                    pass

                            st.session_state[draft_seq_key] += 1
                            st.session_state[draft_key] = {
                                "draft_id": st.session_state[draft_seq_key],
                                "mode": "files",
                                "date": parsed_date,
                                "date_detected": date_detected,
                                "dates_found": parsed.get("dates_found", []),
                                "values": {k: parsed.get(k) for k in ALL_MARKER_KEYS},
                                "auto_keys": [k for k in ALL_MARKER_KEYS if parsed.get(k) is not None],
                                "filename": " + ".join(parsed.get("filenames", [])) or uploaded_files[0].name,
                                "lab_name": parsed.get("lab_name"),
                                "markers_found": parsed.get("markers_found", 0),
                                "extraction_engine": parsed.get("extraction_engine", "local"),
                                "per_file_results": parsed.get("per_file_results", []),
                                "warnings": parsed.get("warnings", []),
                                "errors": parsed.get("errors", []),
                                "raw_text": parsed.get("raw_text", ""),
                                "notes": "",
                            }
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Error inesperado al leer el archivo: {exc}")

        st.markdown("<div style='text-align:center; color:#6C7D93; font-size:0.78rem; margin:8px 0;'>— o —</div>", unsafe_allow_html=True)
        if st.button(
            "✍️ Ingresar los valores manualmente (sin archivo)",
            use_container_width=True,
            key=f"{key_prefix}_btn_manual_bw",
        ):
            st.session_state[draft_seq_key] += 1
            st.session_state[draft_key] = {
                "draft_id": st.session_state[draft_seq_key],
                "mode": "manual",
                "date": datetime.now().date(),
                "date_detected": False,
                "dates_found": [],
                "values": {k: None for k in ALL_MARKER_KEYS},
                "auto_keys": [],
                "filename": None,
                "lab_name": None,
                "markers_found": 0,
                "extraction_engine": "manual",
                "per_file_results": [],
                "warnings": [],
                "errors": [],
                "raw_text": "",
                "notes": "",
            }
            st.rerun()
        return

    # ═══════════════════════════════════════════════════════════════
    #  PASO 2: REVISAR Y CONFIRMAR DATOS ANTES DE GUARDAR
    # ═══════════════════════════════════════════════════════════════
    draft_id = draft["draft_id"]
    found_cnt = draft.get("markers_found", 0)
    engine_used = draft.get("extraction_engine", "local")
    auto_keys = set(draft.get("auto_keys", []))

    col_hdr1, col_hdr2 = st.columns([2.6, 1.4])
    with col_hdr1:
        if draft["mode"] == "manual":
            st.info("✍️ **Ingreso manual:** completa los biomarcadores que tengas en tu resultado y verifica la fecha.")
        elif found_cnt > 0:
            engine_lbl = "🤖 Lectura Inteligente IA + Validación" if engine_used == "ia_hibrida" else "📄 Lectura Automática"
            lab_part = f" · Lab: **{draft['lab_name']}**" if draft.get("lab_name") else ""
            st.success(
                f"✅ **{found_cnt} de 16 biomarcadores detectados** ({engine_lbl}){lab_part}.\n\n"
                f"Revisa los valores con marca `✓` abajo, corrige si hace falta y presiona **Guardar**."
            )
        else:
            st.warning(
                "⚠️ **No logramos leer números automáticamente de este archivo** (puede ser una foto borrosa, recortada o un formato inusual). "
                "Puedes digitar los resultados manualmente abajo o volver a subir una foto/PDF más nítido."
            )
    with col_hdr2:
        if st.button("↩️ Cambiar archivos", use_container_width=True, key=f"{key_prefix}_cancel_draft_{draft_id}"):
            _reset_wizard()
            st.rerun()

    # Mostrar detalle por archivo si se subieron varios o hubo errores parciales
    per_files = draft.get("per_file_results", [])
    if len(per_files) > 1 or draft.get("errors"):
        lines_pf = []
        for pf in per_files:
            if pf["ok"]:
                dt_txt = f" · fecha {pf['date']}" if pf.get("date") else ""
                lines_pf.append(f"- ✅ `{pf['filename']}`: {pf['markers_found']} marcadores{dt_txt}")
            else:
                lines_pf.append(f"- ❌ `{pf['filename']}`: {pf['error']}")
        st.caption("Detalle por archivo:\n" + "\n".join(lines_pf))

    if draft["mode"] == "files" and not draft.get("date_detected"):
        st.warning(
            "📅 **Verifica la fecha del examen:** no pudimos identificarla automáticamente en el documento, "
            "así que pusimos la fecha de hoy temporalmente."
        )

    for w_msg in draft.get("warnings", []):
        st.warning(f"⚠️ {w_msg}")

    # Vista previa de semáforo de los marcadores detectados
    if auto_keys:
        chips = []
        for k in ALL_MARKER_KEYS:
            v = draft["values"].get(k)
            if v is not None and k in ranges:
                r_info = ranges[k]
                lbl, col_hex = classify_bloodwork_value(k, v, gender)
                chips.append(
                    f'<span style="display:inline-block; margin:2px 4px 2px 0; padding:3px 8px; border-radius:8px; '
                    f'background:{col_hex}18; border:1px solid {col_hex}66; color:#FFFFFF; font-size:0.74rem;">'
                    f'{r_info["emoji"]} <b>{r_info["name"].split("(")[0].strip()}:</b> '
                    f'<span style="color:{col_hex}; font-weight:800;">{_fmt_val(v)}</span> {r_info["unit"]}</span>'
                )
        if chips:
            _render_html(
                '<div style="background:rgba(16,20,30,0.9); border:1px solid rgba(255,255,255,0.1); '
                'border-radius:10px; padding:10px 12px; margin-bottom:12px;">'
                '<div style="color:#8E9BAE; font-size:0.72rem; font-weight:700; margin-bottom:6px; text-transform:uppercase;">'
                'Vista rápida de valores leídos</div>'
                + "".join(chips)
                + "</div>"
            )

    # Formulario editable de confirmación
    num_cols = 3 if is_coach else 2
    with st.form(f"{key_prefix}_review_form_{draft_id}", clear_on_submit=False):
        c_date, c_info = st.columns([1, 1.2])
        with c_date:
            selected_date = st.date_input(
                "📅 Fecha del examen (toma de muestra):",
                value=draft["date"],
                key=f"{key_prefix}_date_{draft_id}",
            )
        with c_info:
            fname_disp = _short_filename(draft.get("filename"), 45)
            st.markdown(
                f"<div style='padding-top:26px; color:#94A3B8; font-size:0.82rem;'>📎 Origen: <b>{fname_disp}</b></div>",
                unsafe_allow_html=True,
            )

        edited_values = {}
        for section_title, items in MARKER_SECTIONS:
            st.markdown(f"##### {section_title}")
            for row_start in range(0, len(items), num_cols):
                row_items = items[row_start : row_start + num_cols]
                cols = st.columns(len(row_items))
                for col_obj, (m_key, step_val) in zip(cols, row_items):
                    r_info = ranges[m_key]
                    init_v = clean_numeric_marker(draft["values"].get(m_key))
                    check_mark = " ✓" if m_key in auto_keys else ""
                    label = f"{r_info['emoji']} {r_info['name']} ({r_info['unit']}){check_mark}"
                    with col_obj:
                        edited_values[m_key] = st.number_input(
                            label,
                            min_value=0.0,
                            max_value=None,
                            value=float(init_v) if init_v is not None else None,
                            step=step_val,
                            help=f"Referencia {gender_badge}: {r_info['help']}",
                            placeholder="Vacío si no aplica",
                            key=f"{key_prefix}_in_{m_key}_{draft_id}",
                        )

        notes_val = st.text_area(
            "📝 Notas / Observaciones (opcional — ej. ayuno, entrenamiento previo, laboratorio):",
            value=draft.get("notes") or (f"Lab: {draft['lab_name']}" if draft.get("lab_name") else ""),
            height=70,
            key=f"{key_prefix}_notes_{draft_id}",
        )

        submitted = st.form_submit_button(
            "💾 Verificar y Guardar Examen",
            type="primary",
            use_container_width=True,
        )

    if draft.get("raw_text") and not draft["raw_text"].startswith("[Lectura directa"):
        with st.expander("🔍 Ver texto extraído del documento (diagnóstico)", expanded=False):
            st.text_area("Texto leído:", value=draft["raw_text"], height=140, disabled=True, key=f"{key_prefix}_raw_{draft_id}")

    if submitted:
        clean_vals = extract_marker_dict(edited_values)
        filled_now = sum(1 for v in clean_vals.values() if v is not None)
        if filled_now == 0:
            st.error("⚠️ Debes ingresar al menos un biomarcador con valor mayor a 0 antes de guardar.")
            return

        # Verificar duplicados contra los exámenes existentes del atleta
        try:
            with SessionLocal() as session:
                existing_recs = (
                    session.query(BloodworkRecord)
                    .filter(BloodworkRecord.member_id == member_id)
                    .order_by(BloodworkRecord.date.desc(), BloodworkRecord.id.desc())
                    .all()
                )
                dup_matches = find_potential_duplicates(
                    existing_records=existing_recs,
                    exam_date=selected_date,
                    new_values=clean_vals,
                    filename=draft.get("filename"),
                )

                if dup_matches:
                    # Serializar matches sin mantener objetos ORM ligados a sesión cerrada
                    safe_matches = []
                    for m in dup_matches:
                        m_copy = dict(m)
                        m_copy.pop("record", None)
                        safe_matches.append(m_copy)

                    # Guardar lo que editó el usuario en el borrador por si vuelve atrás
                    draft["date"] = selected_date
                    draft["values"] = clean_vals
                    draft["notes"] = notes_val
                    st.session_state[draft_key] = draft

                    st.session_state[pending_key] = {
                        "date": selected_date,
                        "values": clean_vals,
                        "filename": draft.get("filename"),
                        "notes": notes_val,
                        "matches": safe_matches,
                    }
                    st.rerun()
                else:
                    _, msg = save_or_merge_bloodwork(
                        session,
                        BloodworkRecord,
                        member_id=member_id,
                        exam_date=selected_date,
                        values=clean_vals,
                        pdf_filename=draft.get("filename"),
                        notes=notes_val,
                        mode="create",
                    )
                    _reset_wizard()
                    who = f" para {athlete_name}" if (is_coach and athlete_name) else ""
                    st.session_state[flash_key] = f"🎉 {msg}{who}"
                    st.toast("Examen guardado correctamente", icon="🎉")
                    st.rerun()
        except Exception as save_err:
            st.error(f"Error al guardar el examen: {save_err}")


# ═══════════════════════════════════════════════════════════════════
#  3. GESTOR AMIGABLE DE EXÁMENES GUARDADOS (COMPLETAR / EDITAR / BORRAR)
# ═══════════════════════════════════════════════════════════════════

def render_bloodwork_manager(
    records: list,
    member_id: int,
    gender: str,
    ranges: dict,
    SessionLocal,
    BloodworkRecord,
    key_prefix: str = "ath",
    is_coach: bool = False,
):
    """
    Permite al atleta o entrenador gestionar sus exámenes ya guardados:
      - Unificar duplicados con 1 clic
      - Completar un examen existente subiendo un PDF/foto adicional
      - Editar fecha, valores de los 16 biomarcadores y notas
      - Eliminar un registro erróneo o duplicado con confirmación segura
    """
    flash_key = f"{key_prefix}_mgr_flash"
    if st.session_state.get(flash_key):
        st.success(st.session_state.pop(flash_key))

    render_duplicate_cleanup_banner(
        records=records,
        member_id=member_id,
        SessionLocal=SessionLocal,
        BloodworkRecord=BloodworkRecord,
        key_prefix=f"{key_prefix}_mgr",
    )

    if not records:
        st.info("Aún no tienes exámenes registrados para gestionar.")
        return

    # Selector claro de examen
    options_map = {}
    for r in records:
        dt_str = r.date.strftime("%d/%m/%Y") if r.date else "Sin fecha"
        cnt = count_record_markers(r)
        src = _short_filename(r.pdf_filename, 28)
        label = f"📅 {dt_str} · {cnt}/16 biomarcadores · {src} (ID: {r.id})"
        options_map[label] = r.id

    sel_label = st.selectbox(
        "Selecciona el examen que deseas gestionar:",
        list(options_map.keys()),
        key=f"{key_prefix}_mgr_select",
    )
    sel_id = options_map[sel_label]
    rec = next((r for r in records if r.id == sel_id), None)
    if not rec:
        return

    # ── A. COMPLETAR CON OTRO PDF / FOTO ──────────────────────────
    missing_cnt = len(ALL_MARKER_KEYS) - count_record_markers(rec)
    with st.expander(
        f"📎 Completar este examen subiendo otro PDF o foto (faltan {missing_cnt} de 16 marcadores)",
        expanded=False,
    ):
        st.caption(
            "Ideal si tu laboratorio te entregó los resultados en varios archivos (por ejemplo, primero el hemograma y luego ferritina o perfil lipídico). "
            "Solo completaremos los datos sin duplicar el examen."
        )
        comp_ver_key = f"{key_prefix}_comp_ver_{sel_id}"
        if comp_ver_key not in st.session_state:
            st.session_state[comp_ver_key] = 0

        comp_files = st.file_uploader(
            "Adjunta PDF(s) o foto(s) complementarios:",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"{key_prefix}_comp_files_{sel_id}_v{st.session_state[comp_ver_key]}",
        )
        comp_pwd = None
        if comp_files:
            parser_mod = _load_parser()
            has_locked = any(
                f.name.lower().endswith(".pdf") and parser_mod.is_pdf_encrypted(f.getvalue())
                for f in comp_files
            )
            if has_locked:
                comp_pwd = st.text_input(
                    "🔒 Contraseña del PDF protegido:",
                    type="password",
                    key=f"{key_prefix}_comp_pwd_{sel_id}_{st.session_state[comp_ver_key]}",
                )

            if st.button(
                "⚡ Leer y Combinar en este Examen",
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_comp_btn_{sel_id}",
            ):
                with st.spinner("🤖 Extrayendo biomarcadores adicionales..."):
                    try:
                        parser_mod = _load_parser()
                        parsed_comp = parser_mod.process_multiple_files(
                            [(f.getvalue(), f.name) for f in comp_files],
                            passwords=comp_pwd if comp_pwd else None,
                            use_ai=True,
                        )
                        if parsed_comp.get("errors") and not parsed_comp.get("filenames"):
                            for e_m in parsed_comp["errors"]:
                                st.error(f"⚠️ {e_m}")
                        elif parsed_comp.get("markers_found", 0) == 0:
                            st.warning("⚠️ No se detectaron biomarcadores en el archivo adjunto. Puedes ingresarlos manualmente abajo.")
                        else:
                            with SessionLocal() as session:
                                _, msg = save_or_merge_bloodwork(
                                    session,
                                    BloodworkRecord,
                                    member_id=member_id,
                                    exam_date=rec.date or datetime.now().date(),
                                    values=parsed_comp,
                                    pdf_filename=" + ".join(parsed_comp.get("filenames", [])),
                                    notes=None,
                                    mode="merge_fill",
                                    target_record_id=sel_id,
                                    overwrite_conflicts=False,
                                )
                            st.session_state[comp_ver_key] += 1
                            st.session_state[flash_key] = f"🎉 {msg}"
                            st.toast("Examen completado exitosamente", icon="✅")
                            st.rerun()
                    except Exception as comp_err:
                        st.error(f"Error al combinar archivo: {comp_err}")

    # ── B. EDITAR FECHA Y BIOMARCADORES ───────────────────────────
    with st.expander("✏️ Editar fecha, biomarcadores o notas de este examen", expanded=True):
        num_cols = 3 if is_coach else 2
        with st.form(f"{key_prefix}_edit_form_{sel_id}"):
            new_date = st.date_input(
                "📅 Fecha del examen:",
                value=rec.date or datetime.now().date(),
                key=f"{key_prefix}_ed_date_{sel_id}",
            )

            ed_vals = {}
            for section_title, items in MARKER_SECTIONS:
                st.markdown(f"##### {section_title}")
                for row_start in range(0, len(items), num_cols):
                    row_items = items[row_start : row_start + num_cols]
                    cols = st.columns(len(row_items))
                    for col_obj, (m_key, step_val) in zip(cols, row_items):
                        r_info = ranges[m_key]
                        curr_v = clean_numeric_marker(getattr(rec, m_key, None))
                        with col_obj:
                            ed_vals[m_key] = st.number_input(
                                f"{r_info['emoji']} {r_info['name']} ({r_info['unit']})",
                                min_value=0.0,
                                max_value=None,
                                value=float(curr_v) if curr_v is not None else None,
                                step=step_val,
                                help=r_info["help"],
                                key=f"{key_prefix}_ed_{m_key}_{sel_id}",
                            )

            ed_notes = st.text_area(
                "📝 Notas / Observaciones:",
                value=rec.notes or "",
                height=70,
                key=f"{key_prefix}_ed_notes_{sel_id}",
            )

            if st.form_submit_button("💾 Guardar Cambios del Examen", type="primary", use_container_width=True):
                clean_ed = extract_marker_dict(ed_vals)
                if sum(1 for v in clean_ed.values() if v is not None) == 0:
                    st.error("⚠️ El examen debe conservar al menos un biomarcador. Si deseas borrarlo, usa la sección Eliminar abajo.")
                else:
                    try:
                        with SessionLocal() as session:
                            r_db = session.query(BloodworkRecord).filter(
                                BloodworkRecord.id == sel_id,
                                BloodworkRecord.member_id == member_id,
                            ).first()
                            if r_db:
                                r_db.date = new_date
                                for k in ALL_MARKER_KEYS:
                                    setattr(r_db, k, clean_ed[k])
                                r_db.notes = ed_notes.strip() if ed_notes and ed_notes.strip() else None
                                session.commit()
                                st.session_state[flash_key] = (
                                    f"✅ Examen del {new_date.strftime('%d/%m/%Y')} actualizado correctamente."
                                )
                                st.toast("Cambios guardados", icon="✅")
                                st.rerun()
                    except Exception as err_ed:
                        st.error(f"Error actualizando examen: {err_ed}")

    # ── C. ELIMINAR EXAMEN (CON CONFIRMACIÓN SEGURA) ──────────────
    with st.expander("🗑️ Eliminar este examen (duplicado o erróneo)", expanded=False):
        dt_del = rec.date.strftime("%d/%m/%Y") if rec.date else "Sin fecha"
        st.warning(f"Estás a punto de eliminar el examen del **{dt_del}** ({count_record_markers(rec)}/16 biomarcadores).")
        confirm_del = st.checkbox(
            f"Confirmo que deseo eliminar permanentemente el examen del {dt_del}",
            key=f"{key_prefix}_confirm_del_{sel_id}",
        )
        if st.button(
            "🗑️ Eliminar Examen Permanentemente",
            type="primary",
            disabled=not confirm_del,
            use_container_width=True,
            key=f"{key_prefix}_btn_del_{sel_id}",
        ):
            try:
                with SessionLocal() as session:
                    r_del = session.query(BloodworkRecord).filter(
                        BloodworkRecord.id == sel_id,
                        BloodworkRecord.member_id == member_id,
                    ).first()
                    if r_del:
                        session.delete(r_del)
                        session.commit()
                st.session_state[flash_key] = f"🗑️ Examen del {dt_del} eliminado correctamente."
                st.toast("Examen eliminado", icon="🗑️")
                st.rerun()
            except Exception as err_del:
                st.error(f"Error al eliminar el examen: {err_del}")
