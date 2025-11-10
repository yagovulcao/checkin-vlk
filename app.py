import os
import unicodedata
from io import BytesIO
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import streamlit as st
from PIL import Image
from supabase import create_client, Client


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Check-in.Vlk", page_icon="📋", layout="centered")
st.title("📋 Check-in.Vlk")

def get_secret(key: str, default: str | None = None):
    try:
        return st.secrets[key]  # Streamlit Cloud
    except Exception:
        return os.getenv(key, default)

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "")
SUPABASE_SERVICE_KEY = get_secret("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Config ausente: defina SUPABASE_URL e SUPABASE_ANON_KEY nos Secrets/variáveis.")
    st.stop()

BUCKET = "photos"
TIMEZONE = ZoneInfo("America/Belem")
UTC = ZoneInfo("UTC")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client | None = None
if SUPABASE_SERVICE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception:
        supabase_admin = None


# =========================
# ESTILIZAÇÃO
# =========================
def inject_css():
    st.markdown(
        """
        <style>
        .vlk-card { padding: 12px; border-radius: 12px; border: 1px solid #e8e8ef;
                    background: #fafafa; margin: 8px 0; }
        .vlk-title { font-weight: 700; font-size: 18px; }
        .vlk-subtle { color: #6a6a75; }
        .stTextInput > div > div > input,
        .stTextArea textarea, .stDateInput input {
            border-radius: 10px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
inject_css()


# =========================
# FUNÇÕES
# =========================
def show_image(img, caption=None):
    try:
        st.image(img, caption=caption, use_container_width=True)
    except TypeError:
        st.image(img, caption=caption)

def agora():
    return datetime.now(TIMEZONE)

def resize_max(image: Image.Image, max_side: int = 1024) -> Image.Image:
    w, h = image.size
    m = max(w, h)
    if m <= max_side:
        return image
    scale = max_side / float(m)
    return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

def upload_photo(image: Image.Image, storage_path: str) -> str:
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    image = resize_max(image, 1024)
    bio = BytesIO()
    image.save(bio, format="JPEG", quality=85, optimize=True)
    data = bio.getvalue()

    supabase.storage.from_(BUCKET).upload(storage_path, data)
    return storage_path

def ensure_bucket():
    try:
        supabase.storage.from_(BUCKET).list("")
    except Exception:
        st.error(f"Bucket '{BUCKET}' não encontrado no Supabase.")
        st.stop()
ensure_bucket()


# ---------- Banco de Dados ----------
def cadastrar_usuario(name: str, role: str, phone: str, email: str | None):
    payload = {
        "name": name.strip(),
        "role": role.strip() if role else None,
        "phone": phone.strip() if phone else None,
        "email": (email or "").strip().lower() or None,
    }
    if payload["email"]:
        res = supabase.table("users").upsert(payload, on_conflict="email").execute()
    else:
        res = supabase.table("users").insert(payload).execute()
    return (res.data or [None])[0]

def listar_usuarios():
    res = supabase.table("users").select("*").order("name").execute()
    return res.data or []

def registrar_checkin(user_id: str, photo_path: str):
    supabase.table("checkins").insert({"user_id": user_id, "photo_path": photo_path}).execute()

def buscar_checkins(limit=2000, dt_from=None, dt_to=None, nome_like=None):
    q = supabase.table("checkins").select(
        "id, created_at, photo_path, user_id, users(id, name, role)"
    ).order("created_at", desc=True)

    if dt_from:
        q = q.gte("created_at", f"{dt_from} 00:00:00+00")
    if dt_to:
        q = q.lte("created_at", f"{dt_to} 23:59:59+00")

    q = q.limit(limit)
    res = q.execute()
    rows = res.data or []

    if nome_like:
        termo = nome_like.lower()
        rows = [r for r in rows if termo in ((r.get("users") or {}).get("name") or "").lower()]

    return rows

def tem_checkin_recente(user_id: str, minutos: int = 30) -> bool:
    now_local = agora()
    start_local = now_local - timedelta(minutes=minutos)
    res = (
        supabase.table("checkins")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", start_local.astimezone(UTC).isoformat())
        .lt("created_at", now_local.astimezone(UTC).isoformat())
        .limit(1)
        .execute()
    )
    return bool(res.data)


# ---------- Agrupamento para Admin ----------
def agrupar_por_usuario_dia(rows):
    grouped = defaultdict(lambda: {"name": "", "role": "", "dias": defaultdict(list)})
    for r in rows:
        u = r.get("users") or {}
        uid = r.get("user_id") or u.get("id")
        if not uid:
            continue

        created = r["created_at"]
        try:
            dt_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(TIMEZONE)
        except Exception:
            dt_local = agora()

        dia = dt_local.strftime("%Y-%m-%d")

        grouped[uid]["name"] = u.get("name") or "—"
        grouped[uid]["role"] = u.get("role") or "—"
        grouped[uid]["dias"][dia].append(
            {"photo_path": r["photo_path"], "created_at": dt_local, "checkin_id": r["id"]}
        )
    return grouped


# =========================
# ADMIN GATE (senha + link)
# =========================
if "admin_ok" not in st.session_state:
    st.session_state.admin_ok = False

try:
    qp = st.query_params
    admin_flag = (qp.get("admin") == "1")
except Exception:
    admin_flag = False

def is_admin():
    return admin_flag and st.session_state.admin_ok

def admin_gate():
    if not admin_flag:
        return False
    if st.session_state.admin_ok:
        return True

    with st.sidebar:
        st.subheader("🔒 Admin login")
        pwd = st.text_input("Senha do Admin", type="password")

        if st.button("Entrar"):
            if pwd == ADMIN_PASSWORD:
                st.session_state.admin_ok = True
                st.success("Acesso liberado.")
            else:
                st.error("Senha inválida.")
    return st.session_state.admin_ok


# =========================
# UI — Abas Públicas
# =========================
tab_cadastro, tab_registro = st.tabs(["✍️ Cadastro", "✅ Registro"])

# -------- CADASTRO --------
with tab_cadastro:
    st.subheader("Cadastrar novo colaborador")

    with st.form("cadastro_form", clear_on_submit=True):
        name = st.text_input("Nome completo*", placeholder="Ex.: João Silva")
        role = st.text_input("Função", placeholder="Ex.: Produção")
        phone = st.text_input("Telefone", placeholder="(xx) xxxxx-xxxx")
        email = st.text_input("Email (opcional)", placeholder="email@empresa.com")
        submitted = st.form_submit_button("Salvar cadastro")

    if submitted:
        if not name.strip():
            st.error("O nome é obrigatório.")
        else:
            try:
                user = cadastrar_usuario(name, role, phone, email)
                if user:
                    st.success(f"✅ Cadastro salvo: {user['name']}")
                else:
                    st.warning("Não foi possível salvar agora.")
            except Exception as e:
                st.error(f"Falha ao cadastrar: {e}")


# -------- REGISTRO --------
with tab_registro:
    st.subheader("Registro de presença com foto")

    usuarios = listar_usuarios()
    if not usuarios:
        st.info("Nenhum usuário cadastrado.")
        st.stop()

    # busca acento-insensível
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

    busca = st.text_input("Digite seu nome:", placeholder="Ex.: Maria / Joao / Ana...")
    filtrados = [u for u in usuarios if not busca or norm(busca) in norm(u["name"])]

    if not filtrados:
        st.info("Nenhum nome encontrado.")
        st.stop()

    if len(filtrados) == 1:
        user = filtrados[0]
        st.success(f"Selecionado: {user['name']}")
    else:
        def label(u): return f"{u['name']} — {u.get('role') or 'Sem função'}"
        escolha = st.radio("Selecione seu nome:", [label(u) for u in filtrados])
        user = filtrados[[label(u) for u in filtrados].index(escolha)]

    st.write(f"**Email:** {user.get('email') or '—'}")
    st.write(f"**Telefone:** {user.get('phone') or '—'}")

    foto = st.camera_input("📸 Tire uma foto (use a câmera frontal)")

    if foto:
        img = Image.open(foto)
        show_image(img, caption="Pré-visualização")

        # CONFIRMAÇÃO VISUAL
        telefone = user.get("phone") or ""
        telefone_final = telefone[-4:] if telefone else "sem telefone"
        confirmacao = st.checkbox(f"✅ Confirmo que sou **{user['name']}** (final {telefone_final})")

        if tem_checkin_recente(user["id"], minutos=30):
            st.error("Você já registrou há menos de 30 minutos.")
        else:
            if st.button("✅ Confirmar e enviar", disabled=not confirmacao):
                ts = agora()
                storage_path = f"{user['id']}/{ts.strftime('%Y-%m-%d')}/{ts.strftime('%H%M%S%f')}.jpg"

                try:
                    upload_photo(img, storage_path)
                    registrar_checkin(user["id"], storage_path)
                    st.success("✅ Presença registrada com sucesso!")
                except Exception as e:
                    st.error(f"Falha ao registrar: {e}")


# =========================
# ADMIN — GALERIA + EXCLUSÃO
# =========================
if admin_gate() and is_admin():
    st.markdown("---")
    st.header("🛠 Painel Admin")

    colf, colt, coln = st.columns([1, 1, 1])
    with colf:
        dt_from = st.date_input("De:", value=None)
    with colt:
        dt_to = st.date_input("Até:", value=None)
    with coln:
        nome_like = st.text_input("Buscar por nome", placeholder="ex.: Maria")

    st.caption("Modo pastas: Usuário → Dia. Selecione fotos para excluir.")
    limit = st.slider("Qtd máxima", min_value=200, max_value=5000, value=2000, step=100)

    try:
        rows = buscar_checkins(limit=limit, dt_from=dt_from, dt_to=dt_to, nome_like=nome_like or None)

        if not rows:
            st.info("Sem registros para os filtros.")
        else:
            grouped = agrupar_por_usuario_dia(rows)

            if "to_delete" not in st.session_state:
                st.session_state.to_delete = set()

            for uid, meta in grouped.items():
                with st.expander(f"👤 {meta['name']} — {meta['role']}", expanded=False):
                    for dia in sorted(meta["dias"].keys(), reverse=True):
                        reg_dia = meta["dias"][dia]
                        data_br = datetime.strptime(dia, "%Y-%m-%d").strftime("%d/%m/%Y")
                        st.markdown(f"<div class='vlk-card'><div class='vlk-title'>📅 {data_br}</div></div>", unsafe_allow_html=True)

                        cols = st.columns(3)
                        idx = 0
                        for item in reg_dia:
                            hora = item["created_at"].strftime("%H:%M")
                            public = supabase.storage.from_(BUCKET).get_public_url(item["photo_path"])

                            with cols[idx]:
                                show_image(public, caption=hora)
                                key = f"del_{item['checkin_id']}"
                                if st.checkbox("Excluir", key=key):
                                    st.session_state.to_delete.add((item["checkin_id"], item["photo_path"]))
                                else:
                                    st.session_state.to_delete.discard((item["checkin_id"], item["photo_path"]))

                            idx = (idx + 1) % 3

            st.markdown("---")
            st.write(f"Selecionadas para exclusão: **{len(st.session_state.to_delete)}**")

            if st.button("🗑️ Excluir selecionadas", type="primary", disabled=len(st.session_state.to_delete) == 0):
                ids = [cid for (cid, _) in st.session_state.to_delete]
                paths = [p for (_, p) in st.session_state.to_delete]
                try:
                    client_del = supabase_admin or supabase
                    client_del.table("checkins").delete().in_("id", ids).execute()
                    client_del.storage.from_(BUCKET).remove(paths)
                    st.session_state.to_delete.clear()
                    st.success("Exclusão realizada!")
                except Exception as e:
                    st.error(f"Erro: {e}")

    except Exception as e:
        st.error(f"Erro ao carregar Admin: {e}")
