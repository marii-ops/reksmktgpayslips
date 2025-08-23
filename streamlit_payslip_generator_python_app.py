# reks_payslip_app.py
# Streamlit Payslip Portal for REKS Amusement Com Inc - Marketing Department
# Features:
#  - SQLite storage: employees, users (credentials), payroll
#  - Employee login → view/download own payslip PDF
#  - HR/Admin login → manage employees, set/reset passwords, add/edit/delete payroll, bulk import,
#    merge duplicate payroll entries (by emp_id + period_start + period_end)
#
# Requirements:
#   pip install streamlit pandas reportlab

import os
import io
import sqlite3
import binascii
import hashlib
from datetime import datetime, date

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors

# ---------------------- Configuration ----------------------
DB_PATH = "reks_payslip.db"
COMPANY_NAME = "REKS Amusement Com Inc"
DEPARTMENT = "Marketing Department"

# Default admin username/password for first-run (override via Streamlit secrets)
DEFAULT_ADMIN_USERNAME = "ADMIN"
DEFAULT_ADMIN_PASSWORD = "RAMS2024!!"

# ---------------------- Utility / Security ----------------------
def _random_salt(n_bytes: int = 16) -> str:
    return binascii.hexlify(os.urandom(n_bytes)).decode()

def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

# ---------------------- DB Helpers ----------------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id TEXT UNIQUE,
        full_name TEXT NOT NULL,
        position TEXT,
        department TEXT,
        rate_type TEXT,
        base_rate REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payroll (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        basic_pay REAL DEFAULT 0,
        overtime_pay REAL DEFAULT 0,
        allowances REAL DEFAULT 0,
        sss REAL DEFAULT 0,
        philhealth REAL DEFAULT 0,
        pagibig REAL DEFAULT 0,
        tax REAL DEFAULT 0,
        other_deductions REAL DEFAULT 0,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        role TEXT NOT NULL, -- 'employee' or 'admin'
        salt TEXT NOT NULL,
        pwd_hash TEXT NOT NULL,
        emp_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
    )
    """)

    conn.commit()
    conn.close()

def ensure_admin_user():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    count = cur.fetchone()[0]
    if count == 0:
        username = st.secrets.get("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME) if hasattr(st, "secrets") else DEFAULT_ADMIN_USERNAME
        password = st.secrets.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD) if hasattr(st, "secrets") else DEFAULT_ADMIN_PASSWORD
        salt = _random_salt()
        pwd_hash = _hash_password(password, salt)
        cur.execute("INSERT INTO users (username, role, salt, pwd_hash) VALUES (?, 'admin', ?, ?)",
                    (username, salt, pwd_hash))
        conn.commit()
    conn.close()

# ---------------------- Data Access ----------------------
def peso(amount):
    try:
        return f"₱{float(amount):,.2f}"
    except Exception:
        return "₱0.00"

def upsert_employee(emp_id, full_name, position="", department="", rate_type="", base_rate=0.0):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO employees (emp_id, full_name, position, department, rate_type, base_rate)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(emp_id) DO UPDATE SET
      full_name=excluded.full_name,
      position=excluded.position,
      department=excluded.department,
      rate_type=excluded.rate_type,
      base_rate=excluded.base_rate
    """, (str(emp_id).strip(), str(full_name).strip(), str(position).strip(), str(department).strip(), str(rate_type).strip(), float(base_rate or 0)))
    conn.commit()
    conn.close()

def delete_employee(emp_id):
    conn = get_conn()
    cur = conn.cursor()
    # delete payroll records and user reference as well
    cur.execute("DELETE FROM payroll WHERE emp_id=?", (str(emp_id).strip(),))
    cur.execute("DELETE FROM users WHERE emp_id=?", (str(emp_id).strip(),))
    cur.execute("DELETE FROM employees WHERE emp_id=?", (str(emp_id).strip(),))
    conn.commit()
    conn.close()

def set_employee_password(emp_id, new_password):
    emp_id = str(emp_id).strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM employees WHERE emp_id=?", (emp_id,))
    if not cur.fetchone():
        conn.close()
        raise ValueError("Employee not found")
    salt = _random_salt()
    pwd_hash = _hash_password(new_password, salt)
    cur.execute("""
    INSERT INTO users (username, role, salt, pwd_hash, emp_id)
    VALUES (?, 'employee', ?, ?, ?)
    ON CONFLICT(username) DO UPDATE SET salt=excluded.salt, pwd_hash=excluded.pwd_hash, emp_id=excluded.emp_id
    """, (emp_id, salt, pwd_hash, emp_id))
    conn.commit()
    conn.close()

def delete_user(username):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=?", (str(username).strip(),))
    conn.commit()
    conn.close()

def insert_or_update_payroll(row):
    """
    Insert a payroll row. If a record exists for same emp_id + period_start + period_end,
    update it (overwrite numeric fields); otherwise insert new.
    """
    emp_id = str(row.get("emp_id") or "").strip()
    period_start = str(row.get("period_start") or "").strip()
    period_end = str(row.get("period_end") or "").strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""SELECT id FROM payroll WHERE emp_id=? AND period_start=? AND period_end=?""",
                (emp_id, period_start, period_end))
    r = cur.fetchone()
    if r:
        pid = r[0]
        cur.execute("""
        UPDATE payroll SET basic_pay=?, overtime_pay=?, allowances=?, sss=?, philhealth=?, pagibig=?, tax=?, other_deductions=?, notes=?, created_at=CURRENT_TIMESTAMP
        WHERE id=?
        """, (
            float(row.get("basic_pay") or 0),
            float(row.get("overtime_pay") or 0),
            float(row.get("allowances") or 0),
            float(row.get("sss") or 0),
            float(row.get("philhealth") or 0),
            float(row.get("pagibig") or 0),
            float(row.get("tax") or 0),
            float(row.get("other_deductions") or 0),
            str(row.get("notes") or ""),
            pid
        ))
    else:
        cur.execute("""
        INSERT INTO payroll (emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            emp_id, period_start, period_end,
            float(row.get("basic_pay") or 0),
            float(row.get("overtime_pay") or 0),
            float(row.get("allowances") or 0),
            float(row.get("sss") or 0),
            float(row.get("philhealth") or 0),
            float(row.get("pagibig") or 0),
            float(row.get("tax") or 0),
            float(row.get("other_deductions") or 0),
            str(row.get("notes") or "")
        ))
    conn.commit()
    conn.close()

def delete_payroll_by_id(pay_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM payroll WHERE id=?", (pay_id,))
    conn.commit()
    conn.close()

def list_employees_df():
    conn = get_conn()
    df = pd.read_sql_query("SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name", conn)
    conn.close()
    return df

def list_payroll_df(emp_id=None):
    conn = get_conn()
    if emp_id:
        df = pd.read_sql_query("SELECT * FROM payroll WHERE emp_id=? ORDER BY period_start DESC", conn, params=(emp_id,))
    else:
        df = pd.read_sql_query("SELECT * FROM payroll ORDER BY created_at DESC", conn)
    conn.close()
    return df

def get_employee(emp_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=?", (str(emp_id).strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"emp_id": row[0], "full_name": row[1], "position": row[2], "department": row[3]}

def verify_login(username, password):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT role, salt, pwd_hash, emp_id FROM users WHERE username=?", (str(username).strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    role, salt, pwd_hash, emp_id = row
    if _hash_password(password, salt) == pwd_hash:
        return {"role": role, "emp_id": emp_id, "username": username}
    return None

# ---------------------- PDF Generation ----------------------
def make_payslip_pdf(company_name, company_address, company_tin, payroll_row: dict, employee_row: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    x0 = margin
    y = height - margin

    def draw_header():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0, y, company_name or "")
        c.setFont("Helvetica", 10)
        y -= 14
        if company_address:
            c.drawString(x0, y, company_address)
            y -= 12
        if company_tin:
            c.drawString(x0, y, f"TIN: {company_tin}")
            y -= 12
        c.line(x0, y, width - margin, y)
        y -= 16

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
        ("Basic Pay", float(payroll_row.get("basic_pay") or 0)),
        ("Overtime Pay", float(payroll_row.get("overtime_pay") or 0)),
        ("Allowances", float(payroll_row.get("allowances") or 0)),
    ]
    deductions = [
        ("SSS", float(payroll_row.get("sss") or 0)),
        ("PhilHealth", float(payroll_row.get("philhealth") or 0)),
        ("Pag-IBIG", float(payroll_row.get("pagibig") or 0)),
        ("Withholding Tax", float(payroll_row.get("tax") or 0)),
        ("Other Deductions", float(payroll_row.get("other_deductions") or 0)),
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
    notes = (payroll_row.get("notes") or "").strip()
    if notes:
        c.setFont("Helvetica", 9)
        c.drawString(x0, y, f"Notes: {notes}")

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} via {COMPANY_NAME} Payslip Portal")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf

# ---------------------- CSV Helpers ----------------------
def safe_read_csv(filelike):
    """Read CSV into DataFrame, coerce column names to lowercase stripped"""
    df = pd.read_csv(filelike, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def merge_duplicate_payrolls():
    """
    Detect duplicates (same emp_id + period_start + period_end) and merge them.
    Merge strategy:
      - Numeric columns are summed
      - notes are concatenated (unique)
      - keep created_at of latest row
    """
    df = list_payroll_df()
    if df.empty:
        return 0

    # normalize keys
    df['key'] = df['emp_id'].astype(str).str.strip() + '|' + df['period_start'].astype(str).str.strip() + '|' + df['period_end'].astype(str).str.strip()

    grouped = df.groupby('key', as_index=False)
    merged_count = 0
    conn = get_conn()
    cur = conn.cursor()

    for key, g in grouped:
        if len(g) <= 1:
            continue
        merged_count += len(g) - 1
        # aggregate
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
        # delete all rows for this key
        ids = tuple(g['id'].astype(int).tolist())
        cur.execute(f"DELETE FROM payroll WHERE id IN ({','.join(['?']*len(ids))})", ids)
        # insert merged
        cur.execute("""
            INSERT INTO payroll (emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (emp_id, ps, pe, basic, ot, allow, sss, phil, pag, tax, other, notes))
    conn.commit()
    conn.close()
    return merged_count

# ---------------------- UI: Login ----------------------
def login_ui():
    st.sidebar.subheader("Sign in")
    role_choice = st.sidebar.selectbox("Login as", ["Employee", "HR/Admin"])
    if role_choice == "Employee":
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
                    st.experimental_rerun()
    else:
        username = st.sidebar.text_input("Username", key="hr_user")
        pwd = st.sidebar.text_input("Password", type="password", key="hr_pwd")
        if st.sidebar.button("Login as HR/Admin", use_container_width=True):
            res = verify_login(username, pwd)
            if not res or res.get("role") != "admin":
                st.sidebar.error("Invalid admin credentials.")
            else:
                st.session_state["auth"] = res
                st.experimental_rerun()

# ---------------------- UI: Dashboards ----------------------
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

    df['period'] = df['period_start'] + " to " + df['period_end']
    period = st.selectbox("Select Pay Period", options=df['period'].tolist())
    row = df[df['period'] == period].iloc[0].to_dict()

    gross = float(row.get("basic_pay") or 0) + float(row.get("overtime_pay") or 0) + float(row.get("allowances") or 0)
    deductions = sum(float(row.get(k) or 0) for k in ["sss", "philhealth", "pagibig", "tax", "other_deductions"])
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
    st.header("Admin Dashboard")
    tabs = st.tabs(["Employees", "Set/Reset Passwords", "Add Payroll", "Bulk Upload", "Merge Duplicates", "All Payroll Records"])

    # Employees management
    with tabs[0]:
        st.subheader("Employees")
        with st.expander("➕ Add / Update Employee"):
            col1, col2 = st.columns(2)
            with col1:
                emp_id = st.text_input("Employee ID *", key="e_empid")
                full_name = st.text_input("Full Name *", key="e_name")
                position = st.text_input("Position", key="e_pos")
            with col2:
                department = st.text_input("Department", key="e_dept")
                rate_type = st.selectbox("Rate Type", ["", "monthly", "daily", "hourly"], index=0, key="e_rate")
                base_rate = st.number_input("Base Rate", min_value=0.0, step=0.01, value=0.0, key="e_base")
            if st.button("Save Employee", type="primary", key="save_emp"):
                if emp_id and full_name:
                    upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                    st.success(f"Saved {full_name} ({emp_id}).")
                else:
                    st.error("Employee ID and Full Name are required.")

        st.write("Existing Employees:")
        emp_df = list_employees_df()
        st.dataframe(emp_df, use_container_width=True)

        st.markdown("### Delete an Employee")
        del_emp_id = st.text_input("Enter Employee ID to delete", key="del_emp")
        if st.button("Delete Employee"):
            if del_emp_id:
                delete_employee(del_emp_id)
                st.success(f"Deleted employee {del_emp_id} and associated payroll & user records.")
            else:
                st.warning("Enter an Employee ID.")

    # Passwords management
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

        st.markdown("Delete a user account (removes login only, leaves employee record):")
        del_user = st.text_input("Username to delete (employee emp_id or admin username)", key="del_user")
        if st.button("Delete User Account"):
            if del_user:
                delete_user(del_user)
                st.success(f"Deleted user {del_user}")
            else:
                st.warning("Enter a username to delete")

    # Add single payroll
    with tabs[2]:
        st.subheader("Add Payroll Entry")
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

    # Bulk upload
    with tabs[3]:
        st.subheader("Bulk Upload (CSV)")
        st.caption("Payroll CSV required columns: emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes")
        uploaded = st.file_uploader("Upload payroll.csv", type=["csv"], key="bulk_pay")
        if uploaded:
            try:
                df = safe_read_csv(uploaded)
                required = {"emp_id", "period_start", "period_end"}
                if not required.issubset(set(df.columns)):
                    st.error(f"CSV must include columns: {', '.join(required)}")
                else:
                    # parse each row safely and insert or update
                    inserted = 0
                    for _, r in df.iterrows():
                        # safe retrieval with fallback
                        row = {
                            "emp_id": str(r.get("emp_id") or "").strip(),
                            "period_start": str(r.get("period_start") or "").strip(),
                            "period_end": str(r.get("period_end") or "").strip(),
                            "basic_pay": float(r.get("basic_pay") or 0),
                            "overtime_pay": float(r.get("overtime_pay") or 0),
                            "allowances": float(r.get("allowances") or 0),
                            "sss": float(r.get("sss") or 0),
                            "philhealth": float(r.get("philhealth") or 0),
                            "pagibig": float(r.get("pagibig") or 0),
                            "tax": float(r.get("tax") or 0),
                            "other_deductions": float(r.get("other_deductions") or 0),
                            "notes": str(r.get("notes") or "").strip()
                        }
                        insert_or_update_payroll(row)
                        inserted += 1
                    st.success(f"Imported/updated {inserted} rows.")
            except Exception as e:
                st.error(f"Failed to import payroll: {e}")

    # Merge duplicates
    with tabs[4]:
        st.subheader("Merge Duplicate Payroll Entries")
        st.markdown("This will find payroll rows with the same emp_id + period_start + period_end and merge them (sums numeric fields).")
        if st.button("Run Merge Duplicates"):
            merged = merge_duplicate_payrolls()
            st.success(f"Merged {merged} duplicate rows." if merged else "No duplicates found.")

    # All payroll view + delete
    with tabs[5]:
        st.subheader("All Payroll Records")
        df_all = list_payroll_df()
        st.dataframe(df_all, use_container_width=True)
        st.markdown("### Delete a payroll record")
        pay_id = st.text_input("Enter payroll record ID to delete (see 'id' column above)", key="del_payid")
        if st.button("Delete payroll record"):
            if pay_id:
                try:
                    delete_payroll_by_id(int(pay_id))
                    st.success(f"Deleted payroll record {pay_id}")
                except Exception as e:
                    st.error(f"Error deleting payroll record: {e}")
            else:
                st.warning("Enter a payroll record id.")

# ---------------------- App Main ----------------------
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

        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("auth", None)
            st.experimental_rerun()

    if "auth" not in st.session_state:
        login_ui()
        return

    # show appropriate dashboard
    role = st.session_state["auth"].get("role")
    if role == "employee":
        employee_dashboard(company_name, company_address, company_tin)
    elif role == "admin":
        hr_dashboard(company_name, company_address, company_tin)
    else:
        st.error("Unknown role in session. Please sign out and sign in again.")

if __name__ == "__main__":
    main()
