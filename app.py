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
        return os.getenv(key, default)  # local

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Config ausente: defina SUPABASE_URL e SUPABASE_ANON_KEY nos Secrets/variáveis.")
    st.stop()

BUCKET = "photos"
TIMEZONE = ZoneInfo("America/Belem")
UTC = ZoneInfo("UTC")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# =========================
# ESTILO (visual polish)
# =========================
def inject_css():
    st.markdown(
        """
        <style>
        /* cards suaves */
        .vlk-card {
            padding: 12px; border-radius: 12px;
            border: 1px solid #e8e8ef; background: #fafafa;
            margin: 8px 0;
        }
        .vlk-title { font-weight: 700; font-size: 18px; }
        .vlk-subtle { color: #6a6a75; }
        .vlk-divider { height:1px; background:#ececf2; margin: 12px 0; }
        /* inputs mais amigáveis */
        .stTextInput > div > div > input,
        .stTextArea textarea, .stSelectbox, .stDateInput input {
            border-radius: 10px !important;
        }
        /* miniaturas mais agradáveis */
        .thumb { border-radius: 10px; border:1px solid #ececf2; }
        </style>
        """,
        unsafe_allow_html=True
    )

inject_css()

# =========================
# HELPERS
# =========================
def show_image(img, caption=None):
    """Exibe imagem adaptando para versões diferentes do Streamlit."""
    try:
        st.image(img, caption=caption, use_container_width=True)
    except TypeError:
        try:
            st.image(img, caption=caption, use_column_width=True)
        except Exception:
            st.image(img, caption=caption)

def agora():
    return datetime.now(TIMEZONE)

def resize_max(image: Image.Image, max_side: int = 1024) -> Image.Image:
    """Redimensiona mantendo proporção para que o maior lado seja no máx. max_side."""
    w, h = image.size
    m = max(w, h)
    if m <= max_side:
        return image
    scale = max_side / float(m)
    new_size = (int(w * scale), int(h * scale))
    return image.resize(new_size, Image.LANCZOS)

def upload_photo(image: Image.Image, storage_path: str) -> str:
    """Redimensiona, salva JPEG otimizado e envia para o bucket."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image = resize_max(image, 1024)

    bio = BytesIO()
    image.save(bio, format="JPEG", quality=85, optimize=True)
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
# DB FUNÇÕES
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

def buscar_checkins(limit=2000, dt_from: date | None = None, dt_to: date | None = None, nome_like: str | None = None):
    """Busca muitos e permite agrupar na UI."""
    q = supabase.table("checkins").select("id, created_at, photo_path, user_id, users(name, role)").order("created_at", desc=True)
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

# Utilitário: checar duplicidade diária
def ja_tem_checkin_hoje(user_id: str) -> bool:
    """Retorna True se já houver check-in do user no dia local (America/Belem)."""
    now_local = agora().replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = now_local
    end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(UTC).isoformat()
    end_utc = end_local.astimezone(UTC).isoformat()

    res = (
        supabase.table("checkins")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", start_utc)
        .lt("created_at", end_utc)
        .limit(1)
        .execute()
    )
    return bool(res.data)

# -------------------------
# AGRUPAMENTO / “PASTAS”
# -------------------------
def agrupar_por_usuario_dia(rows):
    """
    Retorna:
    {
      user_id: {
        'name': 'Maria',
        'role': 'Produção',
        'dias': {
           '2025-11-07': [ {photo_path, created_at}, ... ],
           ...
        }
      },
      ...
    }
    """
    grouped = defaultdict(lambda: {"name": "", "role": "", "dias": defaultdict(list)})
    for r in rows:
        u = r.get("users") or {}
        uid = r.get("user_id")
        nome = u.get("name") or "—"
        role = u.get("role") or "—"
        created = r["created_at"]
        # normaliza dia
        try:
            dt_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(TIMEZONE)
        except Exception:
            dt_local = agora()
        dia = dt_local.strftime("%Y-%m-%d")

        grouped[uid]["name"] = nome
        grouped[uid]["role"] = role
        grouped[uid]["dias"][dia].append(
            {"photo_path": r["photo_path"], "created_at": dt_local}
        )
    return grouped

# -------------------------
# NORMALIZAÇÃO DE STORAGE
# -------------------------
def desired_path(user_id: str, created_at: datetime) -> str:
    return f"{user_id}/{created_at.strftime('%Y-%m-%d')}/{created_at.strftime('%H%M%S%f')}.jpg"

def normalize_storage_paths(rows):
    """
    Move (ou copia+remove) objetos antigos para a estrutura:
    user_id/AAAA-MM-DD/HHMMSSfff.jpg
    e atualiza 'photo_path' no banco.
    """
    moved = 0
    for r in rows:
        uid = r.get("user_id")
        if not uid:
            continue

        # created_at -> timezone já convertido em buscar/agrupamento
        created = r["created_at"]
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(TIMEZONE)
        except Exception:
            dt = agora()

        current_path = r["photo_path"]
        new_path = desired_path(uid, dt)

        if current_path == new_path:
            continue

        storage = supabase.storage.from_(BUCKET)

        # tenta mover diretamente
        try:
            if hasattr(storage, "move"):
                storage.move(current_path, new_path)  # type: ignore
            else:
                raise AttributeError("move not available")
        except Exception:
            # fallback: copy + remove
            try:
                storage.copy(current_path, new_path)
                storage.remove(current_path)
            except Exception:
                continue

        # atualiza no banco
        try:
            supabase.table("checkins").update({"photo_path": new_path}).eq("id", r["id"]).execute()
            moved += 1
        except Exception:
            pass

    return moved

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

            # BLOQUEIO: 1 check-in por dia
            if ja_tem_checkin_hoje(user["id"]):
                st.error("Você já fez o check-in hoje. O registro é permitido apenas 1 vez por dia.")
            else:
                if st.button("✅ Confirmar e enviar"):
                    ts = agora()
                    # SALVA JÁ NA ESTRUTURA "PASTAS": user_id/AAAA-MM-DD/HHMMSSfff.jpg
                    storage_path = f"{user['id']}/{ts.strftime('%Y-%m-%d')}/{ts.strftime('%H%M%S%f')}.jpg"
                    try:
                        upload_photo(img, storage_path)
                        registrar_checkin(user["id"], storage_path)
                        st.success("✅ Presença registrada com sucesso!")
                    except Exception as e:
                        st.error(f"❌ Falha ao registrar: {e}")

# =========================
# UI — ADMIN (oculto + “pastas”)
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

    st.caption("Dica: este modo exibe registros agrupados por **Usuário → Dia** com miniaturas.")
    limit = st.slider("Quantidade máxima a carregar", min_value=200, max_value=5000, value=2000, step=100)

    try:
        rows = buscar_checkins(limit=limit, dt_from=dt_from, dt_to=dt_to, nome_like=nome_like or None)

        # BOTÃO: NORMALIZAR ARQUIVOS ANTIGOS PARA A ESTRUTURA DE PASTAS
        with st.expander("🧹 Organizar storage (mover fotos antigas para /user_id/AAAA-MM-DD/HHMMSS.jpg)"):
            st.write("Use quando houver registros antigos fora da estrutura. Atualiza paths no banco.")
            if st.button("Executar organização agora"):
                moved = normalize_storage_paths(rows)
                st.success(f"Organização concluída. Arquivos movidos/atualizados: {moved}. Recarregue para ver.")

        if not rows:
            st.info("Sem registros para os filtros atuais.")
        else:
            grouped = agrupar_por_usuario_dia(rows)

            # LISTA DE USUÁRIOS (pastas)
            for uid, meta in grouped.items():
                with st.expander(f"👤 {meta['name']} — {meta['role']}", expanded=False):
                    # por dia (mais recente primeiro)
                    for dia in sorted(meta["dias"].keys(), reverse=True):
                        reg_dia = meta["dias"][dia]
                        # título bonito do dia
                        try:
                            data_br = datetime.strptime(dia, "%Y-%m-%d").strftime("%d/%m/%Y")
                        except Exception:
                            data_br = dia
                        st.markdown(f"<div class='vlk-card'><div class='vlk-title'>📅 {data_br}</div></div>", unsafe_allow_html=True)

                        # grade de miniaturas (3 colunas)
                        cols = st.columns(3)
                        cidx = 0
                        for item in reg_dia:
                            created = item["created_at"]
                            hora = created.strftime("%H:%M")
                            public_resp = supabase.storage.from_(BUCKET).get_public_url(item["photo_path"])
                            public_url = (
                                public_resp
                                if isinstance(public_resp, str)
                                else public_resp.public_url
                                if hasattr(public_resp, "public_url")
                                else public_resp.get("publicUrl")
                                if isinstance(public_resp, dict)
                                else None
                            )
                            with cols[cidx]:
                                if public_url:
                                    show_image(public_url, caption=hora)
                                else:
                                    st.write(item["photo_path"])
                            cidx = (cidx + 1) % 3

        # EXPORTAÇÃO CSV
        import pandas as pd
        df = pd.DataFrame(
            [
                {
                    "created_at": r["created_at"],
                    "user_id": r.get("user_id"),
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

# =========================
# IDEIAS DE MELHORIA (TODO)
# =========================
# 1) Assinatura de URL (privacidade): usar create_signed_url(path, expires_in=60) no Admin em vez de bucket público.
# 2) Paginação/lazy load no Admin para períodos muito grandes.
# 3) QR Code/UID por usuário para pular a busca de nome no registro (ideal para filas grandes).
# 4) Exportação ZIP das fotos de um dia/usuário (download por pasta).
# 5) Campo "site/turno" no cadastro, e filtros por local/turno no Admin.
