import os
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid4

import streamlit as st
from PIL import Image
from supabase import create_client, Client
from dotenv import load_dotenv

# =========================================
# CONFIGURAÇÕES
# =========================================
st.set_page_config(page_title="Check-in.Vlk", page_icon="📋", layout="centered")

load_dotenv()  # carrega variáveis do .env

APP_NAME = "Check-in.Vlk"
TIMEZONE = ZoneInfo("America/Belem")
BUCKET = "photos"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

st.title(f"📋 {APP_NAME}")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Config ausente: defina SUPABASE_URL e SUPABASE_ANON_KEY no arquivo .env")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# =========================================
# FUNÇÕES AUXILIARES
# =========================================
def show_image(img, caption=None):
    """Mostra imagem sem quebrar com versões antigas do Streamlit."""
    try:
        st.image(img, caption=caption, use_container_width=True)
    except TypeError:
        st.image(img, caption=caption)

def agora():
    return datetime.now(TIMEZONE)

def upload_photo(image: Image.Image, storage_path: str) -> str:
    """
    Converte imagem para JPEG e faz upload para o Supabase Storage.
    Usa apenas path + bytes (sem file_options) para máxima compatibilidade entre versões.
    """
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    bio = BytesIO()
    image.save(bio, format="JPEG", quality=90)
    data = bio.getvalue()  # bytes

    if not isinstance(storage_path, str):
        storage_path = str(storage_path)

    # Tentativa 1: assinatura comum v2 (upload(path, bytes))
    try:
        supabase.storage.from_(BUCKET).upload(storage_path, data)
    except TypeError:
        # Tentativa 2: algumas builds pedem keywords explícitas
        supabase.storage.from_(BUCKET).upload(path=storage_path, file=data)

    return storage_path

def cadastrar_usuario(name: str, role: str, phone: str, email: str | None):
    payload = {
        "name": name.strip(),
        "role": role.strip() if role else None,
        "phone": phone.strip() if phone else None,
        "email": email.strip().lower() if email else None,
    }
    if email:
        res = supabase.table("users").upsert(payload, on_conflict="email").execute()
    else:
        res = supabase.table("users").insert(payload).execute()
    return res.data[0] if res.data else None

def listar_usuarios():
    res = supabase.table("users").select("*").order("name").execute()
    return res.data or []

def registrar_checkin(user_id: str, photo_path: str):
    supabase.table("checkins").insert({
        "user_id": user_id,
        "photo_path": photo_path,
    }).execute()

def ensure_bucket():
    try:
        # listar raiz do bucket para validar acesso
        supabase.storage.from_(BUCKET).list("")
    except Exception:
        st.error(f"Bucket '{BUCKET}' não encontrado. Crie no Supabase → Storage → Buckets (nome: {BUCKET}, Public).")
        st.stop()

ensure_bucket()

# =========================================
# INTERFACE
# =========================================
tab_cadastro, tab_registro, tab_admin = st.tabs(["✍️ Cadastro", "✅ Registro", "🛠 Admin"])

# ------------ CADASTRO -----------
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
                st.success(f"✅ Cadastro salvo: {user['name']}")
            except Exception as e:
                st.error(f"Falha ao cadastrar: {e}")

# ------------ REGISTRO DE PRESENÇA -----------
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

# ------------ ADMIN (visualização) -----------
with tab_admin:
    st.subheader("Últimos check-ins")
    try:
        res = (
            supabase.table("checkins")
            .select("id, created_at, photo_path, users(name, role)")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        registros = res.data or []
        if not registros:
            st.info("Ainda não há registros.")
        else:
            for r in registros:
                nome = (r.get("users") or {}).get("name")
                funcao = (r.get("users") or {}).get("role") or "—"
                st.write(f"**{nome}** ({funcao}) — {r['created_at']}")

                # get_public_url pode retornar string, objeto ou dict; tratamos todos
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
                    st.write(f"Foto armazenada em: {r['photo_path']}")
    except Exception as e:
        st.error(f"Erro ao carregar registros: {e}")
