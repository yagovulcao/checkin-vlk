import os
import unicodedata
from io import BytesIO
from datetime import datetime, date
from zoneinfo import ZoneInfo

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
        st.error(f"Bucket '{BUCKET}' não encontrado no Supabase.")
        st.stop()

ensure_bucket()


# -------------------------
# DATABASE FUNCTIONS
# -------------------------
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
        termo = nome_like.lower()
        rows = [r for r in rows if termo in ((r.get("users") or {}).get("name") or "").lower()]

    return rows


# =========================
# ADMIN GATE (link + senha)
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


# -------- CADASTRO --------
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


# -------- REGISTRO --------
with tab_registro:
    st.subheader("Registro de presença com foto")

    usuarios = listar_usuarios()

    if not usuarios:
        st.info("Nenhum usuário cadastrado. Vá para a aba *Cadastro*.")
    else:

        # ------- BUSCA (acento-insensível) -------
        def norm(s: str) -> str:
            s = unicodedata.normalize("NFKD", s or "")
            return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

        busca = st.text_input("Digite seu nome ou parte dele:", placeholder="Ex.: Maria / Joao / Ana...")

        filtrados = [u for u in usuarios if not busca or norm(busca) in norm(u["name"])]

        if not filtrados:
            st.info("Nenhum nome encontrado. Tente outra grafia.")
            st.stop()

        if len(filtrados) == 1:
            user = filtrados[0]
            st.success(f"Selecionado: {user['name']}")
        else:
            def label(u):
                role = u.get("role") or "Sem função"
                return f"{u['name']} — {role}"

            labels = [label(u) for u in filtrados]
            escolha = st.radio("Selecione seu nome:", labels, key="escolha_user")
            user = filtrados[labels.index(escolha)]

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
# UI — ADMIN (oculto por URL + senha)
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

    limit = st.slider("Quantidade a exibir", min_value=10, max_value=200, value=50, step=10)

    try:
        rows = buscar_checkins(limit=limit, dt_from=dt_from, dt_to=dt_to, nome_like=nome_like or None)

        if not rows:
            st.info("Sem registros para os filtros atuais.")
        else:
            for r in rows:
                user_data = r.get("users") or {}
                user_name = user_data.get("name") or "—"
                user_role = user_data.get("role") or "—"

                try:
                    dt_utc = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                    dt_local = dt_utc.astimezone(TIMEZONE)
                except Exception:
                    dt_local = agora()

                data_fmt = dt_local.strftime("%d/%m/%Y")
                hora_fmt = dt_local.strftime("%H:%M")

                st.markdown(
                    f"""
                    <div style="padding:12px; border-radius:10px; border:1px solid #e2e2e2; margin-bottom:10px; background:#fafafa;">
                        <div style="font-weight:600; font-size:16px;">{user_name}</div>
                        <div style="color:#555; margin-top:2px;"><em>{user_role}</em></div>
                        <div style="margin-top:6px;">📅 {data_fmt} &nbsp;&nbsp; ⏰ {hora_fmt}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                public_resp = supabase.storage.from_(BUCKET).get_public_url(r["photo_path"])
                public_url = (
                    public_resp
                    if isinstance(public_resp, str)
                    else public_resp.public_url
                    if hasattr(public_resp, "public_url")
                    else public_resp.get("publicUrl")
                    if isinstance(public_resp, dict)
                    else None
                )

                if public_url:
                    show_image(public_url)
                else:
                    st.write(f"Foto: {r['photo_path']}")

        import pandas as pd
        df = pd.DataFrame(
            [
                {
                    "created_at": r["created_at"],
                    "name": (r.get("users") or {}).get("name"),
                    "role": (r.get("users") or {}).get("role"),
                    "photo_path": r["photo_path"],
                }
                for r in rows
            ]
        )

        st.download_button(
            "⬇️ Baixar CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="checkins.csv",
            mime="text/csv",
        )

    except Exception as e:
        st.error(f"Erro ao carregar Admin: {e}")
