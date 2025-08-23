# app.py
# Streamlit Payslip Portal with Login (Employees & HR/Admin)
# - SQLite storage for employees, users (credentials), and payroll
# - HR/Admin can add/update employees, reset passwords, upload/edit payroll
# - Employees can log in to view & download their own PDF payslips
# How to run locally:
#   pip install streamlit pandas reportlab
#   streamlit run app.py
# Deploy: push this file to GitHub and deploy on Streamlit Community Cloud

import io
import os
import sqlite3
import secrets
import binascii
from datetime import datetime, date

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors

DB_PATH = "payslip.db"

# ----------------------------- Security Helpers -----------------------------

def _random_salt(n_bytes: int = 16) -> str:
    return binascii.hexlify(os.urandom(n_bytes)).decode()


def _hash_password(password: str, salt: str) -> str:
    import hashlib
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


# ----------------------------- DB helpers -----------------------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Employees master
    cur.execute(
        """
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
        """
    )

    # Payroll records
    cur.execute(
        """
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
        """
    )

    # Users/credentials (employees + admins)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,        -- for employees, use emp_id; for admin, any username
            role TEXT NOT NULL,          -- 'employee' | 'admin'
            salt TEXT NOT NULL,
            pwd_hash TEXT NOT NULL,
            emp_id TEXT,                 -- null for admin accounts
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
        )
        """
    )

    conn.commit()
    conn.close()


def ensure_admin_exists():
    """Create an admin user if none exists. Uses Streamlit secret ADMIN_PASSWORD if set, else default 'admin/admin'."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    (count,) = cur.fetchone()
    if count == 0:
        username = "ADMIN"
        password = st.secrets.get("ADMIN_PASSWORD", "admin")
        salt = _random_salt()
        pwd_hash = _hash_password(password, salt)
        cur.execute("INSERT INTO users (username, role, salt, pwd_hash) VALUES (?, 'admin', ?, ?)", (username, salt, pwd_hash))
        conn.commit()
    conn.close()


# ----------------------------- Data access helpers -----------------------------

def peso(amount: float) -> str:
    try:
        return f"₱{amount:,.2f}"
    except Exception:
        return "₱0.00"


def upsert_employee(emp_id, full_name, position, department, rate_type, base_rate):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO employees (emp_id, full_name, position, department, rate_type, base_rate)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(emp_id) DO UPDATE SET
            full_name=excluded.full_name,
            position=excluded.position,
            department=excluded.department,
            rate_type=excluded.rate_type,
            base_rate=excluded.base_rate
        """,
        (emp_id.strip(), full_name.strip(), position.strip(), department.strip(), (rate_type or "").strip(), float(base_rate or 0)),
    )
    conn.commit()
    conn.close()


def set_employee_password(emp_id: str, new_password: str):
    emp_id = emp_id.strip()
    conn = get_conn()
    cur = conn.cursor()
    # ensure employee exists
    cur.execute("SELECT 1 FROM employees WHERE emp_id=?", (emp_id,))
    if not cur.fetchone():
        conn.close()
        raise ValueError("Employee not found")
    # upsert user as employee role
    salt = _random_salt()
    pwd_hash = _hash_password(new_password, salt)
    cur.execute(
        """
        INSERT INTO users (username, role, salt, pwd_hash, emp_id)
        VALUES (?, 'employee', ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET salt=excluded.salt, pwd_hash=excluded.pwd_hash, emp_id=excluded.emp_id
        """,
        (emp_id, salt, pwd_hash, emp_id),
    )
    conn.commit()
    conn.close()


def insert_payroll(row: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payroll (
            emp_id, period_start, period_end, basic_pay, overtime_pay, allowances,
            sss, philhealth, pagibig, tax, other_deductions, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(row.get("emp_id")).strip(), str(row.get("period_start")), str(row.get("period_end")),
            float(row.get("basic_pay", 0) or 0), float(row.get("overtime_pay", 0) or 0), float(row.get("allowances", 0) or 0),
            float(row.get("sss", 0) or 0), float(row.get("philhealth", 0) or 0), float(row.get("pagibig", 0) or 0),
            float(row.get("tax", 0) or 0), float(row.get("other_deductions", 0) or 0),
            (row.get("notes") or "").strip(),
        ),
    )
    conn.commit()
    conn.close()


def list_employees_df():
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name",
        conn,
    )
    conn.close()
    return df


def list_payroll_df(emp_id: str | None = None):
    conn = get_conn()
    if emp_id:
        df = pd.read_sql_query(
            "SELECT * FROM payroll WHERE emp_id = ? ORDER BY period_start DESC", conn, params=(emp_id,)
        )
    else:
        df = pd.read_sql_query("SELECT * FROM payroll ORDER BY created_at DESC", conn)
    conn.close()
    return df


def get_employee(emp_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=?", (emp_id.strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"emp_id": row[0], "full_name": row[1], "position": row[2], "department": row[3]}


def verify_login(username: str, password: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT role, salt, pwd_hash, emp_id FROM users WHERE username=?", (username.strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    role, salt, pwd_hash, emp_id = row
    if _hash_password(password, salt) == pwd_hash:
        return {"role": role, "emp_id": emp_id, "username": username}
    return None


# ----------------------------- PDF generation -----------------------------

def make_payslip_pdf(company_name: str, company_address: str, company_tin: str, payroll_row: dict, employee_row: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Margins
    margin = 18 * mm
    x0 = margin
    y = height - margin

    def draw_header():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0, y, company_name or "Company Name")
        c.setFont("Helvetica", 10)
        y -= 14
        if company_address:
            c.drawString(x0, y, company_address)
            y -= 12
        if company_tin:
            c.drawString(x0, y, f"TIN: {company_tin}")
            y -= 16
        c.setStrokeColor(colors.black)
        c.line(x0, y, width - margin, y)
        y -= 16

    def label_value(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x0, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(x0 + 120, y, value)
        y -= 12

    draw_header()

    # Employee + Period
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

    # Earnings & Deductions
    earnings = [
        ("Basic Pay", float(payroll_row.get("basic_pay", 0) or 0)),
        ("Overtime Pay", float(payroll_row.get("overtime_pay", 0) or 0)),
        ("Allowances", float(payroll_row.get("allowances", 0) or 0)),
    ]
    deductions = [
        ("SSS", float(payroll_row.get("sss", 0) or 0)),
        ("PhilHealth", float(payroll_row.get("philhealth", 0) or 0)),
        ("Pag-IBIG", float(payroll_row.get("pagibig", 0) or 0)),
        ("Withholding Tax", float(payroll_row.get("tax", 0) or 0)),
        ("Other Deductions", float(payroll_row.get("other_deductions", 0) or 0)),
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
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} via Streamlit Payslip Portal")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf


# ----------------------------- UI: Auth -----------------------------

def login_ui():
    st.sidebar.subheader("Sign in")
    tab_emp, tab_hr = st.sidebar.tabs(["Employee", "HR/Admin"])

    with tab_emp:
        emp_id = st.text_input("Employee ID", key="emp_login")
        pwd = st.text_input("Password", type="password", key="emp_pwd")
        if st.button("Login as Employee", use_container_width=True):
            if not emp_id or not pwd:
                st.sidebar.error("Enter Employee ID and password.")
            else:
                res = verify_login(emp_id, pwd)
                if not res or res["role"] != "employee":
                    st.sidebar.error("Invalid credentials.")
                else:
                    st.session_state["auth"] = res
                    st.rerun()

    with tab_hr:
        username = st.text_input("Username", key="hr_user")
        pwd = st.text_input("Password", type="password", key="hr_pwd")
        if st.button("Login as HR/Admin", use_container_width=True):
            res = verify_login(username, pwd)
            if not res or res["role"] != "admin":
                st.sidebar.error("Invalid admin credentials.")
            else:
                st.session_state["auth"] = res
                st.rerun()


# ----------------------------- UI: Dashboards -----------------------------

def employee_dashboard(company, address, tin):
    auth = st.session_state.get("auth")
    emp_id = auth.get("emp_id") or auth.get("username")  # employees use their emp_id as username
    emp = get_employee(emp_id)
    if not emp:
        st.error("Employee profile not found. Please contact HR.")
        return

    st.header(f"👤 {emp['full_name']} — {emp_id}")
    df = list_payroll_df(emp_id)
    if df.empty:
        st.info("No payroll records yet.")
        return

    df["period"] = df["period_start"] + " to " + df["period_end"]
    period = st.selectbox("Select Pay Period", options=df["period"].tolist())
    row = df[df["period"] == period].iloc[0].to_dict()

    gross = float(row.get("basic_pay", 0) or 0) + float(row.get("overtime_pay", 0) or 0) + float(row.get("allowances", 0) or 0)
    deductions = sum(float(row.get(k, 0) or 0) for k in ["sss", "philhealth", "pagibig", "tax", "other_deductions"]) 
    net = gross - deductions

    col1, col2, col3 = st.columns(3)
    col1.metric("Gross Pay", peso(gross))
    col2.metric("Total Deductions", peso(deductions))
    col3.metric("Net Pay", peso(net))

    if st.button("Download PDF Payslip", type="primary"):
        pdf_bytes = make_payslip_pdf(company, address, tin, row, emp)
        filename = f"payslip_{emp_id}_{row.get('period_start')}_{row.get('period_end')}.pdf"
        st.download_button(
            label="Click to save PDF",
            data=pdf_bytes,
            file_name=filename,
            mime="application/pdf",
        )


def hr_dashboard(company, address, tin):
    st.header("🛠️ HR / Admin Dashboard")

    tabs = st.tabs(["Employees", "Set/Reset Passwords", "Add Payroll", "Bulk Upload", "All Payroll Records"]) 

    # --- Employees management ---
    with tabs[0]:
        st.subheader("Employees")
        with st.expander("➕ Add / Update Employee"):
            col1, col2 = st.columns(2)
            with col1:
                emp_id = st.text_input("Employee ID *")
                full_name = st.text_input("Full Name *")
                position = st.text_input("Position")
            with col2:
                department = st.text_input("Department")
                rate_type = st.selectbox("Rate Type", ["", "monthly", "daily", "hourly"], index=0)
                base_rate = st.number_input("Base Rate", min_value=0.0, step=0.01, value=0.0)
            if st.button("Save Employee", type="primary"):
                if emp_id and full_name:
                    upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                    st.success(f"Saved {full_name} ({emp_id}).")
                else:
                    st.error("Employee ID and Full Name are required.")
        st.dataframe(list_employees_df(), use_container_width=True)

    # --- Passwords ---
    with tabs[1]:
        st.subheader("Set or Reset Employee Password")
        emp_id_pw = st.text_input("Employee ID")
        new_pw = st.text_input("New Password", type="password")
        if st.button("Save Password"):
            if not emp_id_pw or not new_pw:
                st.error("Enter Employee ID and a password.")
            else:
                try:
                    set_employee_password(emp_id_pw, new_pw)
                    st.success("Password saved.")
                except ValueError as e:
                    st.error(str(e))

    # --- Add Payroll (single) ---
    with tabs[2]:
        st.subheader("Add Payroll Entry")
        emp_list = list_employees_df()
        emp_opts = [f"{r.full_name} ({r.emp_id})" for _, r in emp_list.iterrows()]
        selected = st.selectbox("Select Employee", options=["-"] + emp_opts)
        selected_emp_id = selected.split("(")[-1].rstrip(")") if selected != "-" else None

        col1, col2, col3 = st.columns(3)
        with col1:
            period_start = st.date_input("Period Start", value=date.today())
        with col2:
            period_end = st.date_input("Period End", value=date.today())
        with col3:
            notes = st.text_input("Notes (optional)")

        colA, colB, colC = st.columns(3)
        with colA:
            basic_pay = st.number_input("Basic Pay", min_value=0.0, step=0.01)
            overtime_pay = st.number_input("Overtime Pay", min_value=0.0, step=0.01)
            allowances = st.number_input("Allowances", min_value=0.0, step=0.01)
        with colB:
            sss = st.number_input("SSS", min_value=0.0, step=0.01)
            philhealth = st.number_input("PhilHealth", min_value=0.0, step=0.01)
            pagibig = st.number_input("Pag-IBIG", min_value=0.0, step=0.01)
        with colC:
            tax = st.number_input("Withholding Tax", min_value=0.0, step=0.01)
            other_deductions = st.number_input("Other Deductions", min_value=0.0, step=0.01)

        if st.button("Save Payroll Entry", type="primary"):
            if not selected_emp_id:
                st.error("Select an employee first.")
            else:
                insert_payroll(
                    {
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
                        "notes": notes,
                    }
                )
                st.success("Payroll saved.")

    # --- Bulk upload ---
    with tabs[3]:
        st.subheader("Bulk Upload (CSV)")
        st.caption("Employees CSV columns: emp_id, full_name, position, department, rate_type, base_rate")
        emp_file = st.file_uploader("Upload employees.csv", type=["csv"], key="emp_csv")
        if emp_file is not None:
            try:
                df = pd.read_csv(emp_file)
                required_cols = {"emp_id", "full_name"}
                if not required_cols.issubset(set(df.columns)):
                    st.error("CSV must include at least emp_id, full_name.")
                else:
                    for _, r in df.iterrows():
                        upsert_employee(
                            str(r.get("emp_id")),
                            str(r.get("full_name")),
                            str(r.get("position", "")),
                            str(r.get("department", "")),
                            str(r.get("rate_type", "")),
                            float(r.get("base_rate", 0) or 0),
                        )
                    st.success(f"Imported {len(df)} employees.")
            except Exception as e:
                st.error(f"Failed to import employees: {e}")

        st.caption("Payroll CSV columns: emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes")
        pay_file = st.file_uploader("Upload payroll.csv", type=["csv"], key="pay_csv")
        if pay_file is not None:
            try:
                df2 = pd.read_csv(pay_file)
                required = {"emp_id", "period_start", "period_end"}
                if not required.issubset(set(df2.columns)):
                    st.error("CSV must include emp_id, period_start, period_end.")
                else:
                    for _, r in df2.iterrows():
                        insert_payroll({k: r.get(k) for k in df2.columns})
                    st.success(f"Imported {len(df2)} payroll rows.")
            except Exception as e:
                st.error(f"Failed to import payroll: {e}")

    # --- All payroll ---
    with tabs[4]:
        st.subheader("All Payroll Records")
        st.dataframe(list_payroll_df(), use_container_width=True)


# ----------------------------- App -----------------------------

def main():
    st.set_page_config(page_title="REKS Marketing Payslip Portal", page_icon="💸", layout="wide")
    init_db()
    ensure_admin_exists()

    st.title("💸 Payslip Portal — Login + HR Encoding")
    st.caption("Employees log in to view/download payslips. HR/Admin manages data.")

    with st.sidebar:
        st.markdown("**Company Profile (appears on payslips)**")
        company_name = st.text_input("Company Name", value="Your Company Name")
        company_address = st.text_input("Company Address", value="City, Country")
        company_tin = st.text_input("Company TIN (optional)")
        st.divider()
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("auth", None)
            st.rerun()

    if "auth" not in st.session_state:
        login_ui()
        return

    role = st.session_state["auth"]["role"]
    if role == "employee":
        employee_dashboard(company_name, company_address, company_tin)
    elif role == "admin":
        hr_dashboard(company_name, company_address, company_tin)
    else:
        st.error("Unknown role.")


if __name__ == "__main__":
    main()
