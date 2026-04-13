import os
import re
import traceback
from datetime import date
from dotenv import load_dotenv
import streamlit as st
from sqlalchemy import create_engine, text
from langchain_openai import ChatOpenAI
from langchain_community.utilities.sql_database import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

load_dotenv()

os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGSMITH_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT", "")
os.environ["LANGSMITH_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "")

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Employee SQL Assistant",
    page_icon="👥",
    layout="centered",
)

st.markdown("<style>#MainMenu, footer { visibility: hidden; }</style>", unsafe_allow_html=True)


# ── ENV profiles (loaded once at startup) ─────────────────────────────────────
ENV_PROFILES = {
    "HANA": {
        "host":     os.getenv("db_host", ""),
        "port":     os.getenv("db_port", "443"),
        "user":     os.getenv("db_user", ""),
        "password": os.getenv("db_password", ""),
        "name":     os.getenv("db_name", ""),
    },
    "PostgreSQL": {
        "host":     os.getenv("pg_db_host", ""),
        "port":     os.getenv("pg_db_port", "5432"),
        "user":     os.getenv("pg_db_user", ""),
        "password": os.getenv("pg_db_password", ""),
        "name":     os.getenv("pg_db_name", ""),
    },
}

DB_OPTIONS = ["HANA", "PostgreSQL", "Other HANA", "Other PostgreSQL"]


# ── Session state defaults ─────────────────────────────────────────────────────
def _init_conn_state():
    defaults = {
        "cfg_db_option":  "HANA",
        "cfg_host":       "",
        "cfg_port":       "",
        "cfg_user":       "",
        "cfg_password":   "",
        "cfg_name":       "",
        "cfg_openai":     os.getenv("OPENAI_API_KEY", ""),  # loaded from .env only
        "cfg_saved":      False,
        "form_version":   0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_conn_state()


def _is_manual(option: str) -> bool:
    return option in ("Other HANA", "Other PostgreSQL")


def _db_type(option: str) -> str:
    return "PostgreSQL" if "PostgreSQL" in option else "HANA"


def _connection_str() -> str | None:
    h  = st.session_state.cfg_host
    p  = st.session_state.cfg_port
    u  = st.session_state.cfg_user
    pw = st.session_state.cfg_password
    n  = st.session_state.cfg_name
    if not all([h, p, u, pw, n]):
        return None
    if _db_type(st.session_state.cfg_db_option) == "PostgreSQL":
        return f"postgresql+psycopg2://{u}:{pw}@{h}:{p}/{n}"
    return f"hana://{u}:{pw}@{h}:{p}/{n}"


# ── Helpers ───────────────────────────────────────────────────────────────────
def connection_test(conn_str: str) -> tuple[bool, str]:
    try:
        engine = create_engine(conn_str, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            if "hana" in conn_str:
                conn.execute(text("SELECT 1 FROM DUMMY"))
            else:
                conn.execute(text("SELECT 1"))
        label = "SAP HANA" if "hana" in conn_str else "PostgreSQL"
        return True, f"Connected to {label}"
    except Exception as e:
        return False, f"Connection failed: {e}"


@st.cache_resource
def get_engine(conn_str: str):
    return create_engine(conn_str)


@st.cache_resource
def get_database(conn_str: str) -> SQLDatabase:
    return SQLDatabase.from_uri(conn_str)


@st.cache_resource
def get_agent(conn_str: str, openai_api_key: str):      # ← key is explicit parameter
    db = get_database(conn_str)
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        api_key=openai_api_key,                         # ← passed directly, not from env
    )
    prefix = (
        "You are an expert SQL assistant connected to an employee database. "
        "You can run SELECT queries to answer questions AND run INSERT statements to add new employees "
        "when the user asks you to. Always confirm after a successful insert by showing the new record. "
        "Never DROP or TRUNCATE tables."
    )
    return create_sql_agent(
        llm=llm,
        db=db,
        verbose=False,
        agent_type="openai-tools",
        prefix=prefix,
    )


def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^\+?[\d\s\-().]{7,15}$", phone))


def insert_employee(conn_str: str, data: dict) -> tuple[bool, str, str]:
    try:
        engine = get_engine(conn_str)
        with engine.begin() as conn:
            result  = conn.execute(text("SELECT COALESCE(MAX(EMP_ID), 0) + 1 FROM EMPLOYEE"))
            next_id = result.scalar()
            conn.execute(
                text("""
                    INSERT INTO EMPLOYEE
                        (EMP_ID, FIRST_NAME, LAST_NAME, EMAIL, PHONE_NUMBER,
                         DEPARTMENT, SALARY, HIRE_DATE, JOB_TITLE)
                    VALUES
                        (:emp_id, :first, :last, :email, :phone,
                         :dept, :salary, :hire_date, :jobtitle)
                """),
                {
                    "emp_id":    next_id,
                    "first":     data["first"],
                    "last":      data["last"],
                    "email":     data["email"],
                    "phone":     data["phone"],
                    "dept":      data["dept"],
                    "salary":    data["salary"],
                    "hire_date": data["hire_date"] or date.today(),
                    "jobtitle":  data["jobtitle"],
                },
            )
        return True, f"✅ **{data['first']} {data['last']}** added successfully (EMP_ID: {next_id}).", ""
    except Exception as e:
        return False, f"❌ Insert failed: {e}", traceback.format_exc()


def _reset_form():
    st.session_state["form_version"] = st.session_state.get("form_version", 0) + 1


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🗄️ SQL Assistant")
    st.divider()

    # ── ⚙️ Settings panel ─────────────────────────────────────────────────────
    with st.expander("⚙️ Connection Settings", expanded=not st.session_state.cfg_saved):
        with st.form("conn_settings_form"):

            cfg_db_option = st.selectbox(
                "Database Profile",
                DB_OPTIONS,
                index=DB_OPTIONS.index(st.session_state.cfg_db_option),
                help="HANA / PostgreSQL loads from .env. 'Other' lets you enter details manually.",
            )

            manual = _is_manual(cfg_db_option)

            if manual:
                st.caption(f"Enter connection details for your {_db_type(cfg_db_option)} database.")
                default_port = "5432" if "PostgreSQL" in cfg_db_option else "443"
                cfg_host = st.text_input("Host *",     placeholder="mydb.example.com")
                cfg_port = st.text_input("Port *",     value=default_port)
                cfg_name = st.text_input("Database *", placeholder="employees")
                cfg_user = st.text_input("Username *", placeholder="admin")
                cfg_pw   = st.text_input("Password *", type="password")
            else:
                profile = ENV_PROFILES[cfg_db_option]
                st.caption(f"Loaded from `.env` — `{'pg_db_*' if cfg_db_option == 'PostgreSQL' else 'db_*'}` keys")
                st.code(
                    f"Host:     {profile['host'] or '(not set)'}\n"
                    f"Port:     {profile['port'] or '(not set)'}\n"
                    f"Database: {profile['name'] or '(not set)'}\n"
                    f"Username: {profile['user'] or '(not set)'}\n"
                    f"Password: {'••••••••' if profile['password'] else '(not set)'}",
                    language="text",
                )
                cfg_host = cfg_port = cfg_name = cfg_user = cfg_pw = None

            # ── OpenAI key — write-only ────────────────────────────────────
            st.markdown("**OpenAI**")
            if st.session_state.cfg_openai:
                st.caption("✅ API key is set — enter a new one to replace it.")
            cfg_openai = st.text_input(
                "API Key",
                value="",                               # ← never pre-filled
                type="password",
                placeholder="sk-... (leave blank to keep existing)",
            )

            save = st.form_submit_button("💾 Save & Test", use_container_width=True)

            if save:
                st.session_state.cfg_db_option = cfg_db_option

                if manual:
                    st.session_state.cfg_host     = (cfg_host or "").strip()
                    st.session_state.cfg_port     = (cfg_port or "").strip()
                    st.session_state.cfg_name     = (cfg_name or "").strip()
                    st.session_state.cfg_user     = (cfg_user or "").strip()
                    st.session_state.cfg_password = cfg_pw or ""
                else:
                    profile = ENV_PROFILES[cfg_db_option]
                    st.session_state.cfg_host     = profile["host"]
                    st.session_state.cfg_port     = profile["port"]
                    st.session_state.cfg_name     = profile["name"]
                    st.session_state.cfg_user     = profile["user"]
                    st.session_state.cfg_password = profile["password"]

                # ── API key: only update if user typed something ───────────
                new_key = (cfg_openai or "").strip()
                if new_key:
                    st.session_state.cfg_openai  = new_key
                    os.environ["OPENAI_API_KEY"]  = new_key
                elif not st.session_state.cfg_openai:
                    st.warning("⚠️ OpenAI API key is required.")
                    st.stop()
                # else: blank input + existing key → keep existing silently

                conn_str = _connection_str()
                if not conn_str:
                    st.warning("⚠️ All connection fields are required.")
                else:
                    get_engine.clear()
                    get_database.clear()
                    get_agent.clear()

                    ok_test, msg_test = connection_test(conn_str)
                    if ok_test:
                        st.session_state.cfg_saved = True
                        st.success(f"🟢 {msg_test}")
                    else:
                        st.session_state.cfg_saved = False
                        st.error(f"🔴 {msg_test}")

    # ── Live status ───────────────────────────────────────────────────────────
    conn_str = _connection_str()
    if conn_str and st.session_state.cfg_saved:
        label = "SAP HANA" if _db_type(st.session_state.cfg_db_option) == "HANA" else "PostgreSQL"
        st.success(f"🟢 {label} · {st.session_state.cfg_db_option}")
    elif not conn_str:
        st.warning("⚠️ Configure connection settings above.")
        st.stop()
    else:
        st.warning("⚠️ Settings saved but not yet tested — click Save & Test.")
        st.stop()

    st.divider()

    # ── Add Employee Form ──────────────────────────────────────────────────────
    st.markdown("**➕ Add Employee**")

    insert_result = st.empty()

    v = st.session_state.form_version

    with st.form("add_employee_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            first_name = st.text_input("First name *", key=f"fn_{v}")
        with col2:
            last_name  = st.text_input("Last name *",  key=f"ln_{v}")

        email     = st.text_input("Email *", key=f"em_{v}")
        phone     = st.text_input("Mobile number", placeholder="+1 512 000 0000", key=f"ph_{v}")
        hire_date = st.date_input(
            "Hire date (blank = today)",
            value=None,
            min_value=date(2000, 1, 1),
            max_value=date(2100, 12, 31),
            key=f"hd_{v}",
        )
        jobtitle   = st.selectbox(
            "Job Title",
            ["Developer", "Sr Developer", "Consultant", "Architect",
             "Project Manager", "Delivery Head"],
            key=f"jt_{v}",
        )
        department = st.selectbox(
            "Department",
            ["SAP", "Oracle", "AWS", "AZURE", "AI", "Other"],
            key=f"dp_{v}",
        )
        salary     = st.number_input("Salary", min_value=0, step=1000, value=60000, key=f"sl_{v}")
        debug_mode = st.checkbox("Show error details", value=False)
        submitted  = st.form_submit_button("Add Employee", use_container_width=True)

        if submitted:
            if not first_name or not last_name or not email:
                st.warning("⚠️ First name, last name and email are required.")
            elif phone and not validate_phone(phone):
                st.warning("⚠️ Enter a valid mobile number, e.g. +1 512 000 0000")
            else:
                success, msg, detail = insert_employee(
                    conn_str,
                    {
                        "first":     first_name.strip(),
                        "last":      last_name.strip(),
                        "email":     email.strip(),
                        "phone":     phone.strip() or None,
                        "dept":      department,
                        "salary":    salary,
                        "hire_date": hire_date,
                        "jobtitle":  jobtitle,
                    },
                )
                if success:
                    insert_result.success(msg)
                    _reset_form()
                    st.rerun()
                else:
                    insert_result.error(msg)
                    if debug_mode and detail:
                        st.code(detail, language="text")

    st.divider()
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    st.caption("Powered by LangChain · OpenAI · SAP HANA / PostgreSQL")


# ── Abort if not connected ─────────────────────────────────────────────────────
if not conn_str or not st.session_state.cfg_saved:
    st.stop()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## 👥 Employee Information System")
st.caption("Ask questions or insert employees via the sidebar form — powered by GPT-4o")
st.divider()

# ── Suggested prompts ─────────────────────────────────────────────────────────
with st.expander("💡 Example questions", expanded=False):
    st.markdown("""
- 👥 List all employees  
- 💼 Show top 10 employees by salary  
- 📅 How many employees were hired this year?  
- 📊 Average salary by department  
- ➕ Add Jane Smith, Developer in SAP, salary 75000  
""")

# ── Chat ──────────────────────────────────────────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

for chat in st.session_state.chat_history:
    avatar = "🧑‍💻" if chat["role"] == "user" else "🤖"
    with st.chat_message(chat["role"], avatar=avatar):
        st.markdown(chat["content"])

user_input = st.chat_input("Ask anything, or say 'Add [name] from [dept] with salary [X]'…")

if user_input:
    st.session_state.chat_history.append({"role": "user", "content": user_input})

    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(user_input)

    with st.chat_message("assistant", avatar="🤖"):
        try:
            agent_executor = get_agent(conn_str, st.session_state.cfg_openai)  # ← key passed explicitly
            with st.spinner("Processing…"):
                response = agent_executor.invoke({"input": user_input})
                answer = response["output"]

            st.markdown(answer)
            st.session_state.chat_history.append({"role": "assistant", "content": answer})

        except Exception as e:
            err = f"⚠️ Error: {e}"
            st.error(err)
            st.session_state.chat_history.append({"role": "assistant", "content": err})