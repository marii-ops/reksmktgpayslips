"""
reks_payslip_app_postgres.py
REKS Amusement Com Inc — Marketing Department
Streamlit app using Supabase Postgres for persistence.

How to run:
1) pip install streamlit pandas psycopg2-binary reportlab
2) In Streamlit Cloud app settings -> Secrets, add:

[supabase]
host = "db.sljhsfkniczwbhggbofk.supabase.co"
port = "5432"
dbname = "postgres"
user = "postgres"
password = "YOUR-PASSWORD"

OPTIONAL:
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

3) streamlit run reks_payslip_app_postgres.py
"""

import io
import binascii
import hashlib
from datetime import datetime, date

import pandas as pd
import streamlit as st

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors

import psycopg2
from psycopg2.extras import RealDictCursor

import psycopg2
import os

# --- Connection settings ---
# It’s best practice to store these as Streamlit Secrets, not hardcode them
DB_HOST = st.secrets["DB_HOST"]
DB_NAME = st.secrets["DB_NAME"]
DB_USER = st.secrets["DB_USER"]
DB_PASS = st.secrets["DB_PASS"]
DB_PORT = st.secrets.get("DB_PORT", 5432)  # default PostgreSQL port

# --- Function to create connection ---
def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=DB_PORT
    )

# --- Streamlit App ---
st.title("📊 Supabase PostgreSQL Demo")

try:
    conn = get_connection()
    st.success("✅ Connected to Supabase Database")

    # Example query: fetch first 10 rows from a table
    query = "SELECT * FROM your_table LIMIT 10;"
    df = pd.read_sql(query, conn)

    st.subheader("Sample Data")
    st.dataframe(df)

    conn.close()
except Exception as e:
    st.error(f"❌ Connection failed: {e}")


# -------------------- Config --------------------
COMPANY_NAME = "REKS Amusement Com Inc"
DEPARTMENT = "Marketing Department"

# -------------------- Security helpers --------------------
def _random_salt(n_bytes: int = 16) -> str:
    return binascii.hexlify(__import__("os").urandom(n_bytes)).decode()

def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

# -------------------- DB Connection --------------------
@st.cache_resource(show_spinner=False)
def get_conn():
    """
    Returns a psycopg2 connection using Streamlit secrets under "supabase".
    Make sure you set secrets as documented in the file header.
    """
    cfg = st.secrets["supabase"]
    conn = psycopg2.connect(
        host=cfg["host"],
        dbname=cfg.get("dbname", cfg.get("database", "postgres")),
        user=cfg["user"],
        password=cfg["YOUR-PASSWORD"],
        port=int(cfg.get("port", 5432)),
    )
    return conn

# -------------------- DB initialization --------------------
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # employees table: emp_id is our employee username/key
    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        emp_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        position TEXT,
        department TEXT,
        rate_type TEXT,
        base_rate NUMERIC,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # payroll table: unique constraint on emp_id + period_start + period_end
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payroll (
        id SERIAL PRIMARY KEY,
        emp_id TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        basic_pay NUMERIC DEFAULT 0,
        overtime_pay NUMERIC DEFAULT 0,
        allowances NUMERIC DEFAULT 0,
        sss NUMERIC DEFAULT 0,
        philhealth NUMERIC DEFAULT 0,
        pagibig NUMERIC DEFAULT 0,
        tax NUMERIC DEFAULT 0,
        other_deductions NUMERIC DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (emp_id, period_start, period_end)
    );
    """)

    # users table for logins (username can be emp_id for employees)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        role TEXT NOT NULL, -- 'employee' or 'admin'
        salt TEXT NOT NULL,
        pwd_hash TEXT NOT NULL,
        emp_id TEXT REFERENCES employees(emp_id) ON DELETE SET NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    conn.commit()
    cur.close()

def ensure_admin_user():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1;")
    if cur.fetchone() is None:
        admin_user = st.secrets.get("ADMIN_USERNAME", "admin")
        admin_pwd = st.secrets.get("ADMIN_PASSWORD", "admin")
        salt = _random_salt()
        pwd_hash = _hash_password(admin_pwd, salt)
        cur.execute(
            "INSERT INTO users (username, role, salt, pwd_hash) VALUES (%s, 'admin', %s, %s) ON CONFLICT (username) DO NOTHING;",
            (admin_user, salt, pwd_hash),
        )
        conn.commit()
    cur.close()

# -------------------- Small helpers --------------------
def safe_float(v, default=0.0):
    try:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return float(default)
        return float(v)
    except Exception:
        return float(default)

def peso(amount):
    try:
        return f"₱{float(amount):,.2f}"
    except Exception:
        return "₱0.00"

# -------------------- Data functions --------------------
def upsert_employee(emp_id, full_name, position="", department="", rate_type="", base_rate=0.0):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO employees (emp_id, full_name, position, department, rate_type, base_rate)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (emp_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            position = EXCLUDED.position,
            department = EXCLUDED.department,
            rate_type = EXCLUDED.rate_type,
            base_rate = EXCLUDED.base_rate;
    """, (str(emp_id).strip(), str(full_name).strip(), str(position).strip(), str(department).strip(), str(rate_type).strip(), safe_float(base_rate)))
    conn.commit()
    cur.close()

def delete_employee(emp_id):
    conn = get_conn()
    cur = conn.cursor()
    # This will cascade-delete payroll rows because of FK ON DELETE CASCADE
    cur.execute("DELETE FROM users WHERE emp_id=%s OR username=%s;", (emp_id, emp_id))
    cur.execute("DELETE FROM employees WHERE emp_id=%s;", (emp_id,))
    conn.commit()
    cur.close()

def set_employee_password(emp_id, new_password):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM employees WHERE emp_id=%s;", (emp_id,))
    if cur.fetchone() is None:
        cur.close()
        raise ValueError("Employee not found")
    salt = _random_salt()
    pwd_hash = _hash_password(new_password, salt)
    cur.execute("""
        INSERT INTO users (username, role, salt, pwd_hash, emp_id)
        VALUES (%s, 'employee', %s, %s, %s)
        ON CONFLICT (username) DO UPDATE SET salt=EXCLUDED.salt, pwd_hash=EXCLUDED.pwd_hash, emp_id=EXCLUDED.emp_id;
    """, (emp_id, salt, pwd_hash, emp_id))
    conn.commit()
    cur.close()

def delete_user(username):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=%s;", (username,))
    conn.commit()
    cur.close()

def insert_or_update_payroll(row: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO payroll (emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (emp_id, period_start, period_end) DO UPDATE SET
            basic_pay = EXCLUDED.basic_pay,
            overtime_pay = EXCLUDED.overtime_pay,
            allowances = EXCLUDED.allowances,
            sss = EXCLUDED.sss,
            philhealth = EXCLUDED.philhealth,
            pagibig = EXCLUDED.pagibig,
            tax = EXCLUDED.tax,
            other_deductions = EXCLUDED.other_deductions,
            notes = EXCLUDED.notes,
            created_at = NOW();
    """, (
        str(row.get("emp_id") or "").strip(),
        str(row.get("period_start") or "").strip(),
        str(row.get("period_end") or "").strip(),
        safe_float(row.get("basic_pay")),
        safe_float(row.get("overtime_pay")),
        safe_float(row.get("allowances")),
        safe_float(row.get("sss")),
        safe_float(row.get("philhealth")),
        safe_float(row.get("pagibig")),
        safe_float(row.get("tax")),
        safe_float(row.get("other_deductions")),
        str(row.get("notes") or "")
    ))
    conn.commit()
    cur.close()

def delete_payroll_by_id(pay_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM payroll WHERE id=%s;", (int(pay_id),))
    conn.commit()
    cur.close()

def list_employees_df():
    conn = get_conn()
    df = pd.read_sql("SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name;", conn)
    return df

def list_payroll_df(emp_id=None):
    conn = get_conn()
    if emp_id:
        df = pd.read_sql("SELECT * FROM payroll WHERE emp_id=%s ORDER BY period_start DESC;", conn, params=(emp_id,))
    else:
        df = pd.read_sql("SELECT * FROM payroll ORDER BY created_at DESC;", conn)
    return df

def get_employee(emp_id):
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=%s;", (str(emp_id).strip(),))
        row = cur.fetchone()
        return dict(row) if row else None

def verify_login(username, password):
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT role, salt, pwd_hash, emp_id FROM users WHERE username=%s;", (str(username).strip(),))
        row = cur.fetchone()
        if not row:
            return None
        if _hash_password(password, row["salt"]) == row["pwd_hash"]:
            return {"role": row["role"], "emp_id": row["emp_id"], "username": username}
        return None

# -------------------- PDF payslip generation --------------------
def make_payslip_pdf(company_name, company_address, company_tin, payroll_row: dict, employee_row: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    x0 = margin
    y = height - margin

    def draw_header():
        nonlocal y
        drew = False
        if company_name:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(x0, y, company_name)
            y -= 14; drew = True
        if company_address:
            c.setFont("Helvetica", 10)
            c.drawString(x0, y, company_address)
            y -= 12; drew = True
        if company_tin:
            c.setFont("Helvetica", 10)
            c.drawString(x0, y, f"TIN: {company_tin}")
            y -= 12; drew = True
        if drew:
            c.line(x0, y, width - margin, y)
            y -= 14

    def label_value(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x0, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(x0 + 120, y, str(value))
        y -= 12

    draw_header()

    emp_name = employee_row.get("full_name", "")
    emp_id = employee_row.get("emp_id", "")
    position = employee_row.get("position", "")
    department = employee_row.get("department", "")

    period_start = payroll_row.get("period_start", "")
    period_end = payroll_row.get("period_end", "")

    label_value("Employee Name:", emp_name)
    label_value("Employee ID:", emp_id)
    if position:
        label_value("Position:", position)
    if department:
        label_value("Department:", department)
    label_value("Pay Period:", f"{period_start} to {period_end}")

    y -= 6
    c.line(x0, y, width - margin, y)
    y -= 16

    earnings = [
        ("Basic Pay", safe_float(payroll_row.get("basic_pay"))),
        ("Overtime Pay", safe_float(payroll_row.get("overtime_pay"))),
        ("Allowances", safe_float(payroll_row.get("allowances"))),
    ]
    deductions = [
        ("SSS", safe_float(payroll_row.get("sss"))),
        ("PhilHealth", safe_float(payroll_row.get("philhealth"))),
        ("Pag-IBIG", safe_float(payroll_row.get("pagibig"))),
        ("Withholding Tax", safe_float(payroll_row.get("tax"))),
        ("Other Deductions", safe_float(payroll_row.get("other_deductions"))),
    ]

    gross = sum(v for _, v in earnings)
    total_deductions = sum(v for _, v in deductions)
    net = gross - total_deductions

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "EARNINGS")
    c.drawString(width/2, y, "DEDUCTIONS")
    y -= 12

    c.setFont("Helvetica", 10)
    y_left = y
    for label, val in earnings:
        c.drawString(x0, y_left, label)
        c.drawRightString(width/2 - 10, y_left, peso(val))
        y_left -= 12

    y_right = y
    for label, val in deductions:
        c.drawString(width/2 + 10, y_right, label)
        c.drawRightString(width - margin, y_right, peso(val))
        y_right -= 12

    y = min(y_left, y_right) - 10
    c.line(x0, y, width - margin, y)
    y -= 14

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "Gross Pay:")
    c.drawRightString(width/2 - 10, y, peso(gross))
    c.drawString(width/2 + 10, y, "Total Deductions:")
    c.drawRightString(width - margin, y, peso(total_deductions))

    y -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, y, "NET PAY:")
    c.drawRightString(width - margin, y, peso(net))

    y -= 20
    notes = (str(payroll_row.get("notes") or "")).strip()
    if notes:
        c.setFont("Helvetica", 9)
        c.drawString(x0, y, f"Notes: {notes}")

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} — {COMPANY_NAME} Payroll Portal")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf

# -------------------- CSV helpers & merge duplicates --------------------
def safe_read_csv(filelike):
    df = pd.read_csv(filelike, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def merge_duplicate_payrolls():
    df = list_payroll_df()
    if df.empty:
        return 0
    # normalize dates to strings for grouping
    df['period_start'] = pd.to_datetime(df['period_start']).dt.date.astype(str)
    df['period_end'] = pd.to_datetime(df['period_end']).dt.date.astype(str)
    df['key'] = df['emp_id'].astype(str).str.strip() + '|' + df['period_start'] + '|' + df['period_end']
    grouped = df.groupby('key', as_index=False)
    merged_count = 0
    conn = get_conn()
    cur = conn.cursor()
    for key, g in grouped:
        if len(g) <= 1:
            continue
        merged_count += len(g) - 1
        emp_id = g.iloc[0]['emp_id']
        ps = g.iloc[0]['period_start']
        pe = g.iloc[0]['period_end']
        basic = g['basic_pay'].astype(float).sum()
        ot = g['overtime_pay'].astype(float).sum()
        allow = g['allowances'].astype(float).sum()
        sss = g['sss'].astype(float).sum()
        phil = g['philhealth'].astype(float).sum()
        pag = g['pagibig'].astype(float).sum()
        tax = g['tax'].astype(float).sum()
        other = g['other_deductions'].astype(float).sum()
        notes = ' | '.join([n for n in g['notes'].astype(str).unique() if n and n != 'nan'])
        # Remove duplicates then upsert merged row
        cur.execute("DELETE FROM payroll WHERE emp_id=%s AND period_start=%s AND period_end=%s;", (emp_id, ps, pe))
        cur.execute("""
            INSERT INTO payroll (emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (emp_id, period_start, period_end) DO UPDATE SET
                basic_pay = EXCLUDED.basic_pay,
                overtime_pay = EXCLUDED.overtime_pay,
                allowances = EXCLUDED.allowances,
                sss = EXCLUDED.sss,
                philhealth = EXCLUDED.philhealth,
                pagibig = EXCLUDED.pagibig,
                tax = EXCLUDED.tax,
                other_deductions = EXCLUDED.other_deductions,
                notes = EXCLUDED.notes,
                created_at = NOW();
        """, (emp_id, ps, pe, basic, ot, allow, sss, phil, pag, tax, other, notes))
    conn.commit()
    cur.close()
    return merged_count

# -------------------- Backup / Restore --------------------
def export_employees_csv() -> bytes:
    df = list_employees_df()
    return df.to_csv(index=False).encode("utf-8")

def export_payroll_csv() -> bytes:
    df = list_payroll_df()
    return df.to_csv(index=False).encode("utf-8")

# -------------------- Auth UI --------------------
def login_ui():
    st.sidebar.subheader("Sign in")
    mode = st.sidebar.selectbox("Login as", ["Employee", "HR/Admin"])
    if mode == "Employee":
        emp_id = st.sidebar.text_input("Employee ID", key="emp_login")
        pwd = st.sidebar.text_input("Password", type="password", key="emp_pwd")
        if st.sidebar.button("Login as Employee", use_container_width=True):
            if not emp_id or not pwd:
                st.sidebar.error("Enter Employee ID and password.")
            else:
                res = verify_login(emp_id, pwd)
                if not res or res.get("role") != "employee":
                    st.sidebar.error("Invalid credentials.")
                else:
                    st.session_state["auth"] = res
                    st.rerun()
    else:
        username = st.sidebar.text_input("Username", key="hr_user")
        pwd = st.sidebar.text_input("Password", type="password", key="hr_pwd")
        if st.sidebar.button("Login as HR/Admin", use_container_width=True):
            res = verify_login(username, pwd)
            if not res or res.get("role") != "admin":
                st.sidebar.error("Invalid admin credentials.")
            else:
                st.session_state["auth"] = res
                st.rerun()

# -------------------- Dashboards --------------------
def employee_dashboard(company, address, tin):
    auth = st.session_state.get("auth") or {}
    emp_id = auth.get("emp_id") or auth.get("username")
    emp = get_employee(emp_id)
    if not emp:
        st.error("Employee profile not found. Please contact HR.")
        return

    st.header(f"👤 {emp['full_name']} — {emp_id}")
    df = list_payroll_df(emp_id)
    if df.empty:
        st.info("No payroll records yet.")
        return
    # make readable period column
    df["period_start"] = pd.to_datetime(df["period_start"]).dt.date.astype(str)
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.date.astype(str)
    df["period"] = df["period_start"] + " to " + df["period_end"]
    period = st.selectbox("Select Pay Period", options=df["period"].tolist())
    row = df[df["period"] == period].iloc[0].to_dict()
    gross = safe_float(row.get("basic_pay")) + safe_float(row.get("overtime_pay")) + safe_float(row.get("allowances"))
    deductions = sum(safe_float(row.get(k)) for k in ["sss", "philhealth", "pagibig", "tax", "other_deductions"])
    net = gross - deductions
    col1, col2, col3 = st.columns(3)
    col1.metric("Gross Pay", peso(gross))
    col2.metric("Total Deductions", peso(deductions))
    col3.metric("Net Pay", peso(net))

    if st.button("Download PDF Payslip", type="primary"):
        pdf_bytes = make_payslip_pdf(company, address, tin, row, emp)
        filename = f"payslip_{emp_id}_{row.get('period_start')}_{row.get('period_end')}.pdf"
        st.download_button(label="Click to save PDF", data=pdf_bytes, file_name=filename, mime="application/pdf")

def hr_dashboard(company, address, tin):
    st.header("🛠️ HR / Admin Dashboard")
    tabs = st.tabs([
        "Employees", "Set/Reset Passwords", "Add/Manage Payroll",
        "Bulk Uploads", "Merge Duplicates", "All Payroll Records", "Backup / Restore"
    ])

    # Employees
    with tabs[0]:
        st.subheader("Employees")
        with st.expander("➕ Add / Update Employee"):
            col1, col2 = st.columns(2)
            with col1:
                emp_id = st.text_input("Employee ID *", key="e_empid")
                full_name = st.text_input("Full Name *", key="e_name")
                position = st.text_input("Position", key="e_pos")
            with col2:
                department = st.text_input("Department", key="e_dept", value=DEPARTMENT)
                rate_type = st.selectbox("Rate Type", ["", "monthly", "daily", "hourly"], index=0, key="e_rate")
                base_rate = st.number_input("Base Rate", min_value=0.0, step=0.01, value=0.0, key="e_base")
            if st.button("Save Employee", type="primary", key="save_emp"):
                if emp_id and full_name:
                    upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                    st.success(f"Saved {full_name} ({emp_id}).")
                else:
                    st.error("Employee ID and Full Name are required.")
        st.write("Existing Employees:")
        st.dataframe(list_employees_df(), use_container_width=True)

        st.markdown("### Delete an Employee (removes employee record, user, payroll)")
        del_emp_id = st.text_input("Enter Employee ID to delete", key="del_emp")
        if st.button("Delete Employee"):
            if del_emp_id:
                delete_employee(del_emp_id)
                st.success(f"Deleted employee {del_emp_id} and associated payroll & user records.")
            else:
                st.warning("Enter an Employee ID.")

        st.markdown("### Bulk Upload Employees (CSV)")
        st.caption("CSV required columns: emp_id, full_name (optional: position, department, rate_type, base_rate)")
        emp_file = st.file_uploader("Upload employees.csv", type=["csv"], key="bulk_emp")
        if emp_file is not None:
            try:
                df = safe_read_csv(emp_file)
                required = {"emp_id", "full_name"}
                if not required.issubset(set(df.columns)):
                    st.error(f"CSV must include: {', '.join(required)}")
                else:
                    count = 0
                    for _, r in df.iterrows():
                        upsert_employee(
                            r.get("emp_id"),
                            r.get("full_name"),
                            r.get("position", ""),
                            r.get("department", ""),
                            r.get("rate_type", ""),
                            safe_float(r.get("base_rate"))
                        )
                        count += 1
                    st.success(f"Imported/updated {count} employees.")
            except Exception as e:
                st.error(f"Failed to import employees: {e}")

    # Passwords
    with tabs[1]:
        st.subheader("Set or Reset Employee Password")
        emp_id_pw = st.text_input("Employee ID", key="pw_empid")
        new_pw = st.text_input("New Password", type="password", key="pw_new")
        if st.button("Save Password", key="save_pw"):
            if not emp_id_pw or not new_pw:
                st.error("Enter Employee ID and a password.")
            else:
                try:
                    set_employee_password(emp_id_pw, new_pw)
                    st.success("Password saved.")
                except ValueError as e:
                    st.error(str(e))

        st.markdown("Delete a user account (removes login only):")
        del_user = st.text_input("Username to delete (employee emp_id or admin username)", key="del_user")
        if st.button("Delete User Account"):
            if del_user:
                delete_user(del_user)
                st.success(f"Deleted user {del_user}")
            else:
                st.warning("Enter a username to delete")

    # Add/manage payroll
    with tabs[2]:
        st.subheader("Add or Update Payroll Entry")
        emp_list = list_employees_df()
        emp_opts = [f"{r.full_name} ({r.emp_id})" for _, r in emp_list.iterrows()]
        selected = st.selectbox("Select Employee", options=["-"] + emp_opts, key="pay_select")
        selected_emp_id = selected.split("(")[-1].rstrip(")") if selected != "-" else None

        col1, col2, col3 = st.columns(3)
        with col1:
            period_start = st.date_input("Period Start", value=date.today(), key="p_start")
        with col2:
            period_end = st.date_input("Period End", value=date.today(), key="p_end")
        with col3:
            notes = st.text_input("Notes (optional)", key="p_notes")

        colA, colB, colC = st.columns(3)
        with colA:
            basic_pay = st.number_input("Basic Pay", min_value=0.0, step=0.01, key="p_basic")
            overtime_pay = st.number_input("Overtime Pay", min_value=0.0, step=0.01, key="p_ot")
            allowances = st.number_input("Allowances", min_value=0.0, step=0.01, key="p_allow")
        with colB:
            sss = st.number_input("SSS", min_value=0.0, step=0.01, key="p_sss")
            philhealth = st.number_input("PhilHealth", min_value=0.0, step=0.01, key="p_ph")
            pagibig = st.number_input("Pag-IBIG", min_value=0.0, step=0.01, key="p_pag")
        with colC:
            tax = st.number_input("Withholding Tax", min_value=0.0, step=0.01, key="p_tax")
            other_deductions = st.number_input("Other Deductions", min_value=0.0, step=0.01, key="p_other")

        if st.button("Save Payroll Entry", type="primary", key="save_payroll"):
            if not selected_emp_id:
                st.error("Select an employee first.")
            else:
                insert_or_update_payroll({
                    "emp_id": selected_emp_id,
                    "period_start": str(period_start),
                    "period_end": str(period_end),
                    "basic_pay": basic_pay,
                    "overtime_pay": overtime_pay,
                    "allowances": allowances,
                    "sss": sss,
                    "philhealth": philhealth,
                    "pagibig": pagibig,
                    "tax": tax,
                    "other_deductions": other_deductions,
                    "notes": notes
                })
                st.success("Payroll saved (inserted or updated).")

        st.divider()
        st.subheader("Delete a Payroll Record by ID")
        st.caption("Find the ID in the 'All Payroll Records' tab.")
        pay_id_del = st.text_input("Payroll ID", key="del_pay_input")
        if st.button("Delete Payroll Record", key="del_pay_btn"):
            if pay_id_del:
                try:
                    delete_payroll_by_id(int(pay_id_del))
                    st.success(f"Deleted payroll record {pay_id_del}")
                except Exception as e:
                    st.error(f"Error deleting payroll record: {e}")
            else:
                st.warning("Enter a payroll record id.")

    # Bulk Uploads
    with tabs[3]:
        st.subheader("Bulk Uploads")
        st.markdown("#### Payroll (CSV)")
        st.caption("Required: emp_id, period_start, period_end. Optional numeric fields and notes.")
        pay_file = st.file_uploader("Upload payroll.csv", type=["csv"], key="bulk_pay")
        if pay_file:
            try:
                df = safe_read_csv(pay_file)
                req = {"emp_id", "period_start", "period_end"}
                if not req.issubset(set(df.columns)):
                    st.error(f"Payroll CSV must include columns: {', '.join(req)}")
                else:
                    count = 0
                    for _, r in df.iterrows():
                        row = {
                            "emp_id": str(r.get("emp_id") or "").strip(),
                            "period_start": str(r.get("period_start") or "").strip(),
                            "period_end": str(r.get("period_end") or "").strip(),
                            "basic_pay": safe_float(r.get("basic_pay")),
                            "overtime_pay": safe_float(r.get("overtime_pay")),
                            "allowances": safe_float(r.get("allowances")),
                            "sss": safe_float(r.get("sss")),
                            "philhealth": safe_float(r.get("philhealth")),
                            "pagibig": safe_float(r.get("pagibig")),
                            "tax": safe_float(r.get("tax")),
                            "other_deductions": safe_float(r.get("other_deductions")),
                            "notes": str(r.get("notes") or "").strip()
                        }
                        insert_or_update_payroll(row)
                        count += 1
                    st.success(f"Imported/updated {count} payroll rows.")
            except Exception as e:
                st.error(f"Failed to import payroll: {e}")

        st.divider()
        st.markdown("#### Employees (CSV)")
        st.caption("Required: emp_id, full_name (optional: position, department, rate_type, base_rate)")
        emp_file = st.file_uploader("Upload employees.csv", type=["csv"], key="bulk_emp2")
        if emp_file is not None:
            try:
                df = safe_read_csv(emp_file)
                required = {"emp_id", "full_name"}
                if not required.issubset(set(df.columns)):
                    st.error(f"Employees CSV must include: {', '.join(required)}")
                else:
                    count = 0
                    for _, r in df.iterrows():
                        upsert_employee(
                            r.get("emp_id"),
                            r.get("full_name"),
                            r.get("position", ""),
                            r.get("department", ""),
                            r.get("rate_type", ""),
                            safe_float(r.get("base_rate"))
                        )
                        count += 1
                    st.success(f"Imported/updated {count} employees.")
            except Exception as e:
                st.error(f"Failed to import employees: {e}")

        st.divider()
        st.markdown("#### Download CSV Templates")
        emp_template = pd.DataFrame([{
            "emp_id": "EMP001",
            "full_name": "Juan Dela Cruz",
            "position": "Staff",
            "department": DEPARTMENT,
            "rate_type": "monthly",
            "base_rate": "15000"
        }])
        pay_template = pd.DataFrame([{
            "emp_id": "EMP001",
            "period_start": "2025-08-01",
            "period_end": "2025-08-15",
            "basic_pay": "7500",
            "overtime_pay": "500",
            "allowances": "1000",
            "sss": "600",
            "philhealth": "450",
            "pagibig": "100",
            "tax": "800",
            "other_deductions": "200",
            "notes": "Example row"
        }])
        st.download_button("⬇️ employees_template.csv", emp_template.to_csv(index=False), "employees_template.csv", "text/csv")
        st.download_button("⬇️ payroll_template.csv", pay_template.to_csv(index=False), "payroll_template.csv", "text/csv")

    # Merge duplicates
    with tabs[4]:
        st.subheader("Merge Duplicate Payroll Entries")
        st.caption("Duplicates share same emp_id + period_start + period_end. Merging sums numeric fields & concatenates notes.")
        if st.button("Run Merge"):
            merged = merge_duplicate_payrolls()
            st.success(f"Merged {merged} rows." if merged else "No duplicates found.")

    # All payroll records
    with tabs[5]:
        st.subheader("All Payroll Records")
        df_all = list_payroll_df()
        if not df_all.empty:
            df_all["period_start"] = pd.to_datetime(df_all["period_start"]).dt.date.astype(str)
            df_all["period_end"] = pd.to_datetime(df_all["period_end"]).dt.date.astype(str)
        st.dataframe(df_all, use_container_width=True)

    # Backup / Restore
    with tabs[6]:
        st.subheader("Backup / Restore")
        st.markdown("**Download Backups**")
        st.download_button("⬇️ Download employees.csv", data=export_employees_csv(), file_name="employees_backup.csv", mime="text/csv")
        st.download_button("⬇️ Download payroll.csv", data=export_payroll_csv(), file_name="payroll_backup.csv", mime="text/csv")
        st.divider()
        st.markdown("**Restore from CSV**")
        emp_restore = st.file_uploader("Upload employees.csv", type=["csv"], key="restore_emp")
        if emp_restore is not None and st.button("Restore Employees"):
            try:
                df = pd.read_csv(emp_restore)
                required = {"emp_id", "full_name"}
                if not required.issubset(set(df.columns)):
                    st.error("Employees CSV must include emp_id, full_name")
                else:
                    for _, r in df.iterrows():
                        upsert_employee(
                            r.get("emp_id"),
                            r.get("full_name"),
                            r.get("position", ""),
                            r.get("department", ""),
                            r.get("rate_type", ""),
                            safe_float(r.get("base_rate"))
                        )
                    st.success(f"Employees restored: {len(df)}")
            except Exception as e:
                st.error(f"Restore failed: {e}")

        pay_restore = st.file_uploader("Upload payroll.csv", type=["csv"], key="restore_pay")
        if pay_restore is not None and st.button("Restore Payroll"):
            try:
                dfp = pd.read_csv(pay_restore)
                req = {"emp_id", "period_start", "period_end"}
                if not req.issubset(set(dfp.columns)):
                    st.error("Payroll CSV must include emp_id, period_start, period_end")
                else:
                    for _, r in dfp.iterrows():
                        insert_or_update_payroll({
                            "emp_id": r.get("emp_id"),
                            "period_start": str(r.get("period_start")),
                            "period_end": str(r.get("period_end")),
                            "basic_pay": safe_float(r.get("basic_pay")),
                            "overtime_pay": safe_float(r.get("overtime_pay")),
                            "allowances": safe_float(r.get("allowances")),
                            "sss": safe_float(r.get("sss")),
                            "philhealth": safe_float(r.get("philhealth")),
                            "pagibig": safe_float(r.get("pagibig")),
                            "tax": safe_float(r.get("tax")),
                            "other_deductions": safe_float(r.get("other_deductions")),
                            "notes": str(r.get("notes") or "")
                        })
                    st.success(f"Payroll rows restored: {len(dfp)}")
            except Exception as e:
                st.error(f"Restore failed: {e}")

# -------------------- App main --------------------
def main():
    st.set_page_config(page_title=f"{COMPANY_NAME} Payroll", page_icon="💸", layout="wide")
    init_db()
    ensure_admin_user()

    st.title(f"{COMPANY_NAME} — {DEPARTMENT}")
    st.caption("Payroll Portal — employees download payslips. HR/Admin manages payroll data.")

    with st.sidebar:
        st.markdown("**Company Profile (appears on payslips)**")
        company_name = st.text_input("Company Name", value=COMPANY_NAME)
        company_address = st.text_input("Company Address", value="")
        company_tin = st.text_input("Company TIN (optional)", value="")
        st.divider()
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("auth", None)
            st.rerun()

    if "auth" not in st.session_state:
        login_ui()
        return

    role = st.session_state["auth"].get("role")
    if role == "employee":
        employee_dashboard(company_name, company_address, company_tin)
    elif role == "admin":
        hr_dashboard(company_name, company_address, company_tin)
    else:
        st.error("Unknown role in session. Please sign out and sign in again.")

if __name__ == "__main__":
    main()
