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
# Opcional e recomendado para exclusão no Admin:
SUPABASE_SERVICE_KEY = get_secret("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Config ausente: defina SUPABASE_URL e SUPABASE_ANON_KEY nos Secrets/variáveis.")
    st.stop()

BUCKET = "photos"
TIMEZONE = ZoneInfo("America/Belem")
UTC = ZoneInfo("UTC")

# Cliente padrão (anon) para operações normais
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
# Cliente admin (service role) somente para exclusões, se disponível
supabase_admin: Client | None = None
if SUPABASE_SERVICE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception:
        supabase_admin = None

# =========================
# ESTILO (visual polish)
# =========================
def inject_css():
    st.markdown(
        """
        <style>
        .vlk-card {
            padding: 12px; border-radius: 12px;
            border: 1px solid #e8e8ef; background: #fafafa;
            margin: 8px 0;
        }
        .vlk-title { font-weight: 700; font-size: 18px; }
        .vlk-subtle { color: #6a6a75; }
        .stTextInput > div > div > input,
        .stTextArea textarea, .stSelectbox, .stDateInput input {
            border-radius: 10px !important;
        }
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
    w, h = image.size
    m = max(w, h)
    if m <= max_side:
        return image
    scale = max_side / float(m)
    new_size = (int(w * scale), int(h * scale))
    return image.resize(new_size, Image.LANCZOS)

def upload_photo(image: Image.Image, storage_path: str) -> str:
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
    """Traz também users.id para agrupar corretamente e permitir exclusão."""
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
    start_utc = start_local.astimezone(UTC).isoformat()
    now_utc = now_local.astimezone(UTC).isoformat()
    res = (
        supabase.table("checkins")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", start_utc)
        .lt("created_at", now_utc)
        .limit(1)
        .execute()
    )
    return bool(res.data)

# -------------------------
# AGRUPAMENTO / “PASTAS”
# -------------------------
def agrupar_por_usuario_dia(rows):
    """Agrupa por usuário (id confiável) e por dia local; inclui checkin_id para exclusão."""
    grouped = defaultdict(lambda: {"name": "", "role": "", "dias": defaultdict(list)})
    for r in rows:
        u = r.get("users") or {}
        uid = r.get("user_id") or u.get("id")
        if not uid:
            continue
        nome = u.get("name") or "—"
        role = u.get("role") or "—"
        created = r["created_at"]
        try:
            dt_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(TIMEZONE)
        except Exception:
            dt_local = agora()
        dia = dt_local.strftime("%Y-%m-%d")
        grouped[uid]["name"] = nome
        grouped[uid]["role"] = role
        grouped[uid]["dias"][dia].append(
            {"photo_path": r["photo_path"], "created_at": dt_local, "checkin_id": r["id"]}
        )
    return grouped

# -------------------------
# NORMALIZAÇÃO DE STORAGE (opcional, mantida)
# -------------------------
def desired_path(user_id: str, created_at: datetime) -> str:
    return f"{user_id}/{created_at.strftime('%Y-%m-%d')}/{created_at.strftime('%H%M%S%f')}.jpg"

def normalize_storage_paths(rows):
    moved = 0
    for r in rows:
        u = r.get("users") or {}
        uid = r.get("user_id") or u.get("id")
        if not uid:
            continue
        try:
            dt = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(TIMEZONE)
        except Exception:
            dt = agora()
        current_path = r["photo_path"]
        new_path = desired_path(uid, dt)
        if current_path == new_path:
            continue
        storage = supabase.storage.from_(BUCKET)
        try:
            if hasattr(storage, "move"):
                storage.move(current_path, new_path)  # type: ignore
            else:
                raise AttributeError("move not available")
        except Exception:
            try:
                storage.copy(current_path, new_path)
                storage.remove(current_path)
            except Exception:
                continue
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

    # monta rótulo com confirmação visual: nome + finais do telefone (se houver)
    phone = (user.get("phone") or "").strip()
    finais = phone[-4:] if len(phone) >= 4 else "----"
    botao_confirma = f"✅ Confirmar: Sou {user['name']} (***{finais})"

    # Janela de 30min
    if tem_checkin_recente(user["id"], minutos=30):
        st.error("Você já registrou há menos de 30 minutos. Tente novamente mais tarde.")
    else:
        if st.button(botao_confirma):
            ts = agora()
            storage_path = f"{user['id']}/{ts.strftime('%Y-%m-%d')}/{ts.strftime('%H%M%S%f')}.jpg"
            try:
                upload_photo(img, storage_path)
                registrar_checkin(user["id"], storage_path)
                st.success("✅ Presença registrada com sucesso!")
            except Exception as e:
                st.error(f"❌ Falha ao registrar: {e}")


# =========================
# UI — ADMIN (oculto + “galeria com exclusão manual”)
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

    st.caption("Modo pastas (Usuário → Dia) com miniaturas. Marque as fotos que deseja EXCLUIR e confirme abaixo.")
    limit = st.slider("Quantidade máxima a carregar", min_value=200, max_value=5000, value=2000, step=100)

    try:
        rows = buscar_checkins(limit=limit, dt_from=dt_from, dt_to=dt_to, nome_like=nome_like or None)

        # Organização opcional
        with st.expander("🧹 Organizar storage (mover fotos antigas para /user_id/AAAA-MM-DD/HHMMSS.jpg)"):
            st.write("Use quando houver registros antigos fora da estrutura. Atualiza paths no banco.")
            if st.button("Executar organização agora"):
                moved = normalize_storage_paths(rows)
                st.success(f"Organização concluída. Arquivos movidos/atualizados: {moved}. Recarregue para ver.")

        if not rows:
            st.info("Sem registros para os filtros atuais.")
        else:
            grouped = agrupar_por_usuario_dia(rows)

            # Coletores de seleção
            if "to_delete" not in st.session_state:
                st.session_state.to_delete = set()

            # Render “pastas”
            for uid, meta in grouped.items():
                with st.expander(f"👤 {meta['name']} — {meta['role']}", expanded=False):
                    for dia in sorted(meta["dias"].keys(), reverse=True):
                        reg_dia = meta["dias"][dia]
                        try:
                            data_br = datetime.strptime(dia, "%Y-%m-%d").strftime("%d/%m/%Y")
                        except Exception:
                            data_br = dia
                        st.markdown(f"<div class='vlk-card'><div class='vlk-title'>📅 {data_br}</div></div>", unsafe_allow_html=True)

                        cols = st.columns(3)
                        cidx = 0
                        for item in reg_dia:
                            created = item["created_at"]
                            hora = created.strftime("%H:%M")
                            checkin_id = item["checkin_id"]
                            path = item["photo_path"]
                            public_resp = supabase.storage.from_(BUCKET).get_public_url(path)
                            public_url = (
                                public_resp if isinstance(public_resp, str)
                                else public_resp.public_url if hasattr(public_resp, "public_url")
                                else public_resp.get("publicUrl") if isinstance(public_resp, dict)
                                else None
                            )
                            with cols[cidx]:
                                if public_url:
                                    show_image(public_url, caption=hora)
                                else:
                                    st.write(path)
                                # checkbox para seleção de exclusão
                                key_cb = f"del_{checkin_id}"
                                checked = st.checkbox("Excluir", key=key_cb)
                                if checked:
                                    st.session_state.to_delete.add((checkin_id, path))
                                else:
                                    # desmarca se já estava marcado
                                    st.session_state.to_delete.discard((checkin_id, path))
                            cidx = (cidx + 1) % 3

            st.divider()
            st.write(f"Selecionadas para exclusão: **{len(st.session_state.to_delete)}** foto(s).")

            # Segurança: precisa service key?
            if not supabase_admin:
                st.warning("Para excluir com segurança, configure `SUPABASE_SERVICE_KEY` nos Secrets. Tentarei com a anon key, mas suas RLS/Policies devem permitir DELETE e remoção no Storage.")

            # Confirmação dupla
            col_a, col_b = st.columns([1, 2])
            with col_a:
                ready = st.checkbox("Confirmo que revisei as seleções")
            with col_b:
                if st.button("🗑️ Excluir selecionadas", type="primary", disabled=not ready or len(st.session_state.to_delete) == 0):
                    ids = [cid for (cid, _) in st.session_state.to_delete]
                    paths = [p for (_, p) in st.session_state.to_delete]
                    try:
                        client_del = supabase_admin or supabase
                        # 1) Deletar do banco
                        if ids:
                            client_del.table("checkins").delete().in_("id", ids).execute()
                        # 2) Remover do storage
                        if paths:
                            client_del.storage.from_(BUCKET).remove(paths)
                        st.session_state.to_delete.clear()
                        st.success("Exclusão concluída. Atualize os filtros para recarregar.")
                    except Exception as e:
                        st.error(f"Falha ao excluir: {e}")

        # Exportação CSV
        import pandas as pd
        df = pd.DataFrame(
            [
                {
                    "created_at": r["created_at"],
                    "user_id": r.get("user_id") or (r.get("users") or {}).get("id"),
                    "name": (r.get("users") or {}).get("name"),
                    "role": (r.get("users") or {}).get("role"),
                    "photo_path": r["photo_path"],
                    "checkin_id": r["id"],
                }
                for r in rows
            ]
        )
        st.download_button(
            "⬇️ Baixar CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="checkins.csv",
            mime="text/csv",
if foto:
    img = Image.open(foto)
    show_image(img, caption="Pré-visualização")

    except Exception as e:
        st.error(f"Erro ao carregar Admin: {e}")
