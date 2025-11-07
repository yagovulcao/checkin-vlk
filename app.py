import os
from io import BytesIO
from datetime import datetime, date
from zoneinfo import ZoneInfo
from uuid import uuid4

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
        return os.getenv(key, default)  # local

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Config ausente: defina SUPABASE_URL e SUPABASE_ANON_KEY nos Secrets/variáveis.")
    st.stop()

BUCKET = "photos"
TIMEZONE = ZoneInfo("America/Belem")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# =========================
# HELPERS
# =========================
def show_image(img, caption=None):
    try:
        st.image(img, caption=caption, use_container_width=True)
    except TypeError:
        st.image(img, caption=caption)

def agora():
    return datetime.now(TIMEZONE)

def upload_photo(image: Image.Image, storage_path: str) -> str:
    # converte para JPEG e envia bytes
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    bio = BytesIO()
    image.save(bio, format="JPEG", quality=90)
    data = bio.getvalue()
    if not isinstance(storage_path, str):
        storage_path = str(storage_path)
    try:
        supabase.storage.from_(BUCKET).upload(storage_path, data)
    except TypeError:
        supabase.storage.from_(BUCKET).upload(path=storage_path, file=data)
    return storage_path

def ensure_bucket():
    try:
        supabase.storage.from_(BUCKET).list("")
    except Exception:
        st.error(f"Bucket '{BUCKET}' não encontrado (Storage → Buckets).")
        st.stop()

ensure_bucket()

# DB helpers
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

def buscar_checkins(limit=50, dt_from: date | None = None, dt_to: date | None = None, nome_like: str | None = None):
    q = supabase.table("checkins").select("id, created_at, photo_path, users(name, role)").order("created_at", desc=True)
    if dt_from:
        q = q.gte("created_at", f"{dt_from} 00:00:00+00")
    if dt_to:
        q = q.lte("created_at", f"{dt_to} 23:59:59+00")
    q = q.limit(limit)
    res = q.execute()
    rows = res.data or []
    if nome_like:
        nome_like_low = nome_like.lower()
        rows = [r for r in rows if ((r.get("users") or {}).get("name") or "").lower().find(nome_like_low) >= 0]
    return rows

# =========================
# ADMIN GATE (link + senha)
# =========================
if "admin_ok" not in st.session_state:
    st.session_state.admin_ok = False

# query param ?admin=1
try:
    qp = st.query_params  # streamlit 1.32+
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
            if not ADMIN_PASSWORD:
                st.warning("ADMIN_PASSWORD não configurado nos Secrets.")
            elif pwd == ADMIN_PASSWORD:
                st.session_state.admin_ok = True
                st.success("Acesso liberado.")
            else:
                st.error("Senha inválida.")
    return st.session_state.admin_ok

# =========================
# UI — Abas Públicas
# =========================
tab_cadastro, tab_registro = st.tabs(["✍️ Cadastro", "✅ Registro"])

with tab_cadastro:
    st.subheader("Cadastrar novo colaborador")
    with st.form("cadastro_form", clear_on_submit=True):
        name = st.text_input("Nome completo*", placeholder="Ex.: Maria Silva")
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
                    st.warning("Não foi possível salvar agora. Tente novamente.")
            except Exception as e:
                st.error(f"Falha ao cadastrar: {e}")

with tab_registro:
    st.subheader("Registro de presença com foto")
    usuarios = listar_usuarios()
    if not usuarios:
        st.info("Nenhum usuário cadastrado. Vá para a aba *Cadastro*.")
    else:
        opcoes = {f"{u['name']} — {u.get('role') or 'Sem função'}": u for u in usuarios}
        selecionado = st.selectbox("Selecione seu nome:", list(opcoes.keys()))
        user = opcoes[selecionado]

        st.write(f"**Email:** {user.get('email') or '—'}")
        st.write(f"**Telefone:** {user.get('phone') or '—'}")

        foto = st.camera_input("📸 Tire uma foto (use a câmera frontal do celular)")
        if foto:
            img = Image.open(foto)
            show_image(img, caption="Pré-visualização")
            if st.button("✅ Confirmar e enviar"):
                ts = agora()
                storage_path = f"{user['id']}/{ts.strftime('%Y-%m-%d')}/{ts.strftime('%H%M%S%f')}.jpg"
                try:
                    upload_photo(img, storage_path)
                    registrar_checkin(user["id"], storage_path)
                    st.success("✅ Presença registrada com sucesso!")
                except Exception as e:
                    st.error(f"❌ Falha ao registrar: {e}")

# =========================
# UI — Admin (OCULTO)
# =========================
if admin_gate() and is_admin():
    st.markdown("---")
    st.header("🛠 Painel Admin")

    colf, colt, coln = st.columns([1,1,1])
    with colf:
        dt_from = st.date_input("De:", value=None)
    with colt:
        dt_to = st.date_input("Até:", value=None)
    with coln:
        nome_like = st.text_input("Buscar por nome", placeholder="ex.: Maria")

    limit = st.slider("Quantidade a exibir", min_value=10, max_value=200, value=50, step=10)

    try:
        rows = buscar_checkins(limit=limit, dt_from=dt_from, dt_to=dt_to, nome_like=nome_like or None)
        if not rows:
            st.info("Sem registros para os filtros atuais.")
        else:
            for r in rows:
                user_name = (r.get("users") or {}).get("name") or "—"
                user_role = (r.get("users") or {}).get("role") or "—"
                st.write(f"**{user_name}** ({user_role}) — {r['created_at']}")

                # get_public_url pode variar por versão
                public_resp = supabase.storage.from_(BUCKET).get_public_url(r["photo_path"])
                if isinstance(public_resp, str):
                    public_url = public_resp
                elif hasattr(public_resp, "public_url"):
                    public_url = public_resp.public_url
                elif isinstance(public_resp, dict):
                    public_url = public_resp.get("publicUrl") or public_resp.get("public_url")
                else:
                    public_url = None

                if public_url:
                    show_image(public_url)
                else:
                    st.write(f"Foto: {r['photo_path']}")

        # exportar CSV
        import pandas as pd
        df = pd.DataFrame([
            {
                "created_at": r["created_at"],
                "name": (r.get("users") or {}).get("name"),
                "role": (r.get("users") or {}).get("role"),
                "photo_path": r["photo_path"],
            } for r in rows
        ])
        st.download_button(
            "⬇️ Baixar CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="checkins.csv",
            mime="text/csv",
        )
    except Exception as e:
        st.error(f"Erro ao carregar Admin: {e}")
