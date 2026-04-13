import os
import re
import traceback
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
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

db_user     = os.getenv("db_user")
db_password = os.getenv("db_password")
db_host     = os.getenv("db_host")
db_port     = os.getenv("db_port")
db_name     = os.getenv("db_name")

connection_str = f"hana://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Employee SQL Assistant",
    page_icon="👥",
    layout="centered",
)

st.markdown("<style>#MainMenu, footer { visibility: hidden; }</style>", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def connection_test(conn_str: str) -> tuple[bool, str]:
    try:
        engine = create_engine(conn_str, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM DUMMY"))
        return True, "Connected to SAP HANA"
    except Exception as e:
        return False, f"Connection failed: {e}"


@st.cache_resource
def get_engine(conn_str: str):
    return create_engine(conn_str)


@st.cache_resource
def get_database(conn_str: str) -> SQLDatabase:
    return SQLDatabase.from_uri(conn_str)


@st.cache_resource
def get_agent(conn_str: str):
    db = get_database(conn_str)
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    prefix = (
        "You are an expert SQL assistant connected to an SAP HANA employee database. "
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
    """
    Inserts a new employee using MAX(EMP_ID)+1 for the primary key.
    Returns (success, user_message, debug_detail).
    """
    try:
        engine = get_engine(conn_str)
        with engine.begin() as conn:

            # ── Derive next EMP_ID inside the same transaction ─────────────
            result  = conn.execute(text("SELECT COALESCE(MAX(EMP_ID), 0) + 1 FROM EMPLOYEE"))
            next_id = result.scalar()

            conn.execute(
                text("""
                    INSERT INTO EMPLOYEE
                        (EMP_ID, FIRST_NAME, LAST_NAME, EMAIL, PHONE_NUMBER,
                         DEPARTMENT, SALARY, HIRE_DATE, JOB_TITLE)
                    VALUES
                        (:emp_id, :first, :last, :email, :phone,
                         :dept, :salary, CURRENT_DATE, :jobtitle)
                """),
                {
                    "emp_id":   next_id,
                    "first":    data["first"],
                    "last":     data["last"],
                    "email":    data["email"],
                    "phone":    data["phone"],
                    "dept":     data["dept"],
                    "salary":   data["salary"],
                    "jobtitle": data["jobtitle"],
                },
            )
        return True, f"✅ **{data['first']} {data['last']}** added successfully (EMP_ID: {next_id}).", ""
    except Exception as e:
        return False, f"❌ Insert failed: {e}", traceback.format_exc()


def _reset_form():
    """Clears form fields from session state — called only on successful insert."""
    for key in ["fn", "ln", "em", "ph", "jt", "dp", "sl"]:
        if key in st.session_state:
            del st.session_state[key]


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🗄️ SQL Assistant")
    st.divider()

    ok, message = connection_test(connection_str)
    if ok:
        st.success(f"🟢 {message}")
    else:
        st.error(f"🔴 {message}")

    st.divider()
    st.markdown("**🔌 Connection**")
    st.caption(f"**Host:** {db_host or '—'}")
    st.caption(f"**Database:** {db_name or '—'}")
    st.caption(f"**Port:** {db_port or '—'}")
    st.caption(f"**Schema:** {db_user.upper() if db_user else '—'}")
    st.caption(f"**Model:** gpt-4o")

    # ── Add Employee Form ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("**➕ Add Employee**")

    # Placeholder outside the form so message survives rerun
    insert_result = st.empty()

    with st.form("add_employee_form", clear_on_submit=False):   # ← fields persist on validation error
        col1, col2 = st.columns(2)
        with col1:
            first_name = st.text_input("First name *", key="fn")
        with col2:
            last_name  = st.text_input("Last name *",  key="ln")

        email      = st.text_input("Email *",          key="em")
        phone      = st.text_input("Mobile number", placeholder="+1 512 000 0000", key="ph")
        jobtitle   = st.selectbox(
            "Job Title",
            ["Developer", "Sr Developer", "Consultant", "Architect",
             "Project Manager", "Delivery Head"],
            key="jt",
        )
        department = st.selectbox(
            "Department",
            ["SAP", "Oracle", "AWS", "AZURE", "AI", "Other"],
            key="dp",
        )
        salary     = st.number_input("Salary", min_value=0, step=1000, value=60000, key="sl")
        debug_mode = st.checkbox("Show error details", value=False)
        submitted  = st.form_submit_button("Add Employee", use_container_width=True)

        if submitted:
            if not first_name or not last_name or not email:
                st.warning("⚠️ First name, last name and email are required.")
            elif phone and not validate_phone(phone):
                st.warning("⚠️ Enter a valid mobile number, e.g. +1 512 000 0000")
            else:
                success, msg, detail = insert_employee(
                    connection_str,
                    {
                        "first":    first_name.strip(),
                        "last":     last_name.strip(),
                        "email":    email.strip(),
                        "phone":    phone.strip() or None,
                        "dept":     department,
                        "salary":   salary,
                        "jobtitle": jobtitle,
                    },
                )
                if success:
                    insert_result.success(msg)
                    _reset_form()       # ← clears fields only on success
                    st.rerun()
                else:
                    insert_result.error(msg)
                    if debug_mode and detail:
                        st.code(detail, language="text")

    st.divider()
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    st.caption("Powered by LangChain · OpenAI · SAP HANA")


# ── Abort if DB is down ───────────────────────────────────────────────────────
if not ok:
    st.stop()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## 👥 Employee Information System")
st.caption("Ask questions or insert employees via the sidebar form — powered by GPT-4o & SAP HANA")
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
            agent_executor = get_agent(connection_str)
            with st.spinner("Processing…"):
                response = agent_executor.invoke({"input": user_input})
                answer = response["output"]

            st.markdown(answer)
            st.session_state.chat_history.append({"role": "assistant", "content": answer})

        except Exception as e:
            err = f"⚠️ Error: {e}"
            st.error(err)
            st.session_state.chat_history.append({"role": "assistant", "content": err})