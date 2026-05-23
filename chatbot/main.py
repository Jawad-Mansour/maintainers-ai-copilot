"""Streamlit UI — login, full chat (SSE), memory inspector, widget configuration, admin panel."""

from __future__ import annotations

import json
import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(page_title="Maintainer's AI Copilot", layout="wide", page_icon="🤖")

st.markdown(
    """
<style>
/* ── Global ──────────────────────────────── */
[data-testid="stAppViewContainer"] {
    background: #0f172a;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] {
    background: #1e293b !important;
    border-right: 1px solid #334155;
}
[data-testid="stSidebar"] * { color: #cbd5e1; }

/* ── Typography fixes ─────────────────────── */
h1, h2, h3 { color: #f1f5f9 !important; }
p, label, .stMarkdown { color: #94a3b8; }

/* ── Sidebar nav buttons ─────────────────── */
[data-testid="stSidebar"] .stButton button {
    background: transparent;
    border: 1px solid #334155;
    color: #cbd5e1;
    border-radius: 8px;
    width: 100%;
    text-align: left;
    font-weight: 500;
    transition: background .15s, border-color .15s;
    padding: 8px 14px;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: #334155;
    border-color: #475569;
    color: #f1f5f9;
}

/* ── Main content area ───────────────────── */
.main .block-container {
    padding: 2rem 2.5rem;
    max-width: 1100px;
}

/* ── Chat messages ───────────────────────── */
[data-testid="stChatMessage"] {
    border-radius: 12px;
    margin-bottom: 4px;
}
[data-testid="stChatMessage"][data-testid*="user"] { background: rgba(99,102,241,.12); }

/* ── Input ───────────────────────────────── */
[data-testid="stChatInput"] textarea {
    background: #1e293b;
    border: 1px solid #334155;
    color: #f1f5f9;
    border-radius: 12px;
}
.stTextInput input, .stSelectbox select {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #f1f5f9 !important;
    border-radius: 8px;
}

/* ── Buttons ─────────────────────────────── */
.stButton button[kind="primary"],
.stFormSubmitButton button {
    background: linear-gradient(135deg, #6366f1, #4f46e5) !important;
    border: none !important;
    color: white !important;
    border-radius: 8px;
    font-weight: 600;
    padding: 8px 20px;
    transition: opacity .15s;
}
.stButton button[kind="primary"]:hover,
.stFormSubmitButton button:hover { opacity: .88; }

/* ── Expanders ───────────────────────────── */
[data-testid="stExpander"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
}

/* ── Metrics / stats ─────────────────────── */
[data-testid="metric-container"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px;
}

/* ── DataFrames ──────────────────────────── */
[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }

/* ── Divider ─────────────────────────────── */
hr { border-color: #334155 !important; }

/* ── Info / warning boxes ────────────────── */
[data-testid="stAlert"] { border-radius: 10px; }

/* ── Tabs ────────────────────────────────── */
[data-testid="stTabs"] [role="tab"] { color: #94a3b8; font-weight: 500; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #6366f1 !important; }
[data-testid="stTabs"] [role="tablist"] { border-bottom: 1px solid #334155; }
</style>
""",
    unsafe_allow_html=True,
)

for _k, _v in [
    ("token", None),
    ("role", None),
    ("email", None),
    ("conversation_id", None),
    ("messages", []),
    ("page", "chat"),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Helpers ───────────────────────────────────────────────────────────────────


def _h() -> dict:
    return {"Authorization": f"Bearer {st.session_state.token}"}


def _login(email: str, password: str) -> bool:
    try:
        r = httpx.post(
            f"{API_URL}/auth/login", json={"email": email, "password": password}, timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            st.session_state.token = d["access_token"]
            st.session_state.role = d.get("role", "user")
            st.session_state.email = email
            return True
        st.error(r.json().get("message", "Login failed"))
    except Exception as exc:
        st.error(f"Connection error: {exc}")
    return False


def _register(email: str, password: str) -> bool:
    try:
        r = httpx.post(
            f"{API_URL}/auth/register",
            json={"email": email, "password": password, "role": "user"},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        st.error(r.json().get("message", "Registration failed"))
    except Exception as exc:
        st.error(f"Connection error: {exc}")
    return False


def _new_conv() -> str | None:
    try:
        r = httpx.post(f"{API_URL}/conversations", headers=_h(), timeout=10)
        if r.status_code in (200, 201):
            return r.json()["id"]
    except Exception:
        pass
    return None


# ── Pages ─────────────────────────────────────────────────────────────────────


def page_login() -> None:
    st.title("🤖 Maintainer's AI Copilot")
    t1, t2 = st.tabs(["Sign in", "Register"])
    with t1, st.form("lf"):
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        if (
            st.form_submit_button("Sign in", use_container_width=True)
            and email
            and pw
            and _login(email, pw)
        ):
            st.rerun()
    with t2, st.form("rf"):
        email_r = st.text_input("Email", key="re")
        pw_r = st.text_input("Password", type="password", key="rp")
        if (
            st.form_submit_button("Create account", use_container_width=True)
            and email_r
            and pw_r
            and _register(email_r, pw_r)
        ):
            st.success("Account created — sign in above.")


def page_chat() -> None:
    st.title("💬 Chat")

    if not st.session_state.conversation_id:
        cid = _new_conv()
        if not cid:
            st.error("Could not create conversation — is the API running?")
            return
        st.session_state.conversation_id = cid
        st.session_state.messages = []

    _, col_btn = st.columns([5, 1])
    with col_btn:
        if st.button("New conversation"):
            cid = _new_conv()
            if cid:
                st.session_state.conversation_id = cid
                st.session_state.messages = []
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                with st.expander("Sources"):
                    for s in msg["sources"]:
                        st.markdown(f"- {s}")
            if msg.get("label") and msg["label"] not in ("unknown", ""):
                st.caption(f"Classified: `{msg['label']}`")

    if prompt := st.chat_input("Ask about a pandas issue…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            ph = st.empty()
            full, label, sources = "", "", []
            try:
                with httpx.stream(
                    "POST",
                    f"{API_URL}/chat/stream",
                    headers={**_h(), "Accept": "text/event-stream"},
                    json={"conversation_id": st.session_state.conversation_id, "message": prompt},
                    timeout=90,
                ) as resp:
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                            except Exception:
                                continue
                            if data.get("type") == "token":
                                full += data["content"]
                                ph.markdown(full + "▌")
                            elif data.get("type") == "done":
                                label = data.get("label", "")
                                sources = data.get("sources", [])
                ph.markdown(full)
            except Exception as exc:
                full = f"Error: {exc}"
                ph.error(full)

            if sources:
                with st.expander("Sources"):
                    for s in sources:
                        st.markdown(f"- {s}")
            if label and label not in ("unknown", ""):
                st.caption(f"Classified: `{label}`")

        st.session_state.messages.append(
            {"role": "assistant", "content": full, "label": label, "sources": sources}
        )


def page_memories() -> None:
    st.title("🧠 Memory Inspector")
    st.caption("Long-term semantic memories (pgvector) stored for your account.")

    try:
        r = httpx.get(f"{API_URL}/memories", headers=_h(), timeout=10)
        mems = r.json() if r.status_code == 200 else []
    except Exception as exc:
        st.error(str(exc))
        mems = []

    if not mems:
        st.info("No memories yet. Ask the chatbot to remember something.")
    else:
        for m in mems:
            with st.expander(m["summary"][:80] + ("…" if len(m["summary"]) > 80 else "")):
                st.write(m["summary"])
                st.caption(f"Stored: {m.get('created_at', '—')}")

    st.divider()
    st.subheader("Cross-conversation recall demo")
    q = st.text_input("Test recall query")
    if q:
        try:
            r = httpx.post(
                f"{API_URL}/memories/search",
                headers=_h(),
                json={"query": q, "top_k": 3},
                timeout=10,
            )
            results = r.json() if r.status_code == 200 else []
            if results:
                for res in results:
                    st.success(res["summary"])
            else:
                st.info("No relevant memories found.")
        except Exception as exc:
            st.error(str(exc))


def page_widgets() -> None:
    st.title("🔧 Widget Configuration")
    if st.session_state.role != "admin":
        st.warning("Admin access required.")
        return

    try:
        r = httpx.get(f"{API_URL}/widgets", headers=_h(), timeout=10)
        widgets = r.json() if r.status_code == 200 else []
    except Exception:
        widgets = []

    if widgets:
        st.subheader("Existing widgets")
        for w in widgets:
            with st.expander(f"`{w['id'][:8]}…`  —  {w.get('greeting', '')}"):
                st.json(w)
                base = os.environ.get("API_PUBLIC_URL", "http://localhost:8000")
                st.code(
                    f'<script src="{base}/widget.js" data-widget-id="{w["id"]}"></script>',
                    language="html",
                )
                if st.button("Delete", key=f"del_{w['id']}"):
                    httpx.delete(f"{API_URL}/widgets/{w['id']}", headers=_h(), timeout=10)
                    st.rerun()

    st.divider()
    st.subheader("Create widget")
    with st.form("wf"):
        name = st.text_input("Widget name", value="My Copilot Widget")
        greeting = st.text_input(
            "Greeting", value="Hi! I'm the Maintainer's Copilot. How can I help?"
        )
        origins_raw = st.text_input(
            "Allowed origins (comma-separated)", value="http://localhost:3001"
        )
        color = st.color_picker("Primary color", value="#6366f1")
        position = st.selectbox("Position", ["bottom-right", "bottom-left"])
        if st.form_submit_button("Create"):
            payload = {
                "name": name,
                "greeting": greeting,
                "allowed_origins": [o.strip() for o in origins_raw.split(",") if o.strip()],
                "theme": {"primary_color": color, "position": position},
                "enabled_tools": ["classify_issue", "search_knowledge_base", "extract_entities"],
            }
            try:
                r = httpx.post(f"{API_URL}/widgets", headers=_h(), json=payload, timeout=10)
                if r.status_code in (200, 201):
                    st.success(f"Created: `{r.json()['id']}`")
                    st.rerun()
                else:
                    st.error(r.text)
            except Exception as exc:
                st.error(str(exc))


def page_admin() -> None:
    st.title("⚙️ Admin Panel")
    if st.session_state.role != "admin":
        st.warning("Admin access required.")
        return

    t_users, t_audit = st.tabs(["Invite user", "Audit log"])

    with t_users, st.form("pf"):
        inv_email = st.text_input("User email")
        inv_pw = st.text_input("Password", type="password")
        inv_role = st.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Invite user"):
            try:
                r = httpx.post(
                    f"{API_URL}/admin/invite",
                    headers=_h(),
                    json={"email": inv_email, "password": inv_pw, "role": inv_role},
                    timeout=10,
                )
                if r.status_code in (200, 201):
                    st.success(f"Invited {inv_email} as {inv_role}")
                else:
                    st.error(r.text)
            except Exception as exc:
                st.error(str(exc))

    with t_audit:
        try:
            r = httpx.get(f"{API_URL}/admin/audit-log", headers=_h(), timeout=10)
            entries = r.json() if r.status_code == 200 else []
            if entries:
                df = pd.DataFrame(entries)
                cols = [
                    c for c in ["created_at", "action", "actor_id", "target_id"] if c in df.columns
                ]
                st.dataframe(df[cols], use_container_width=True)
            else:
                st.info("No audit entries yet.")
        except Exception as exc:
            st.error(str(exc))


# ── App shell ─────────────────────────────────────────────────────────────────


def main() -> None:
    if not st.session_state.token:
        page_login()
        return

    with st.sidebar:
        st.markdown(f"**{st.session_state.email}**")
        st.caption(f"Role: {st.session_state.role}")
        st.divider()

        nav = {"💬 Chat": "chat", "🧠 Memories": "memories", "🔧 Widgets": "widgets"}
        if st.session_state.role == "admin":
            nav["⚙️ Admin"] = "admin"

        for lbl, key in nav.items():
            if st.button(lbl, use_container_width=True):
                st.session_state.page = key

        st.divider()
        if st.button("Sign out", use_container_width=True):
            for k in ["token", "role", "email", "conversation_id"]:
                st.session_state[k] = None
            st.session_state.messages = []
            st.rerun()

    dispatch = {
        "chat": page_chat,
        "memories": page_memories,
        "widgets": page_widgets,
        "admin": page_admin,
    }
    dispatch.get(st.session_state.page, page_chat)()


main()
