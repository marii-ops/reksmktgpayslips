# app.py
# Streamlit Payslip Generator with SQLite storage and PDF export (ReportLab)
# Designed for small teams (e.g., ~5 employees). Employees can self-serve their payslips.
# How to run locally:
#   1) pip install streamlit reportlab pandas
#   2) streamlit run app.py
# Optional deploy: Streamlit Community Cloud.

import io
import sqlite3
from datetime import datetime, date

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors

DB_PATH = "payslip.db"

# ----------------------------- DB helpers -----------------------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT UNIQUE,
            full_name TEXT NOT NULL,
            position TEXT,
            department TEXT,
            rate_type TEXT, -- 'monthly' or 'daily' or 'hourly' (for reference only)
            base_rate REAL, -- optional reference value
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
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
    conn.commit()
    conn.close()


# ----------------------------- PDF generation -----------------------------

def peso(amount: float) -> str:
    try:
        return f"₱{amount:,.2f}"
    except Exception:
        return "₱0.00"


def make_payslip_pdf(company_name: str, company_address: str, company_tin: str, payroll_row: dict, employee_row: dict) -> bytes:
    """Return a PDF (bytes) for a single payslip using ReportLab."""
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

    # Earnings & Deductions table
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

    # Titles
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
        y -= 12

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} via Streamlit Payslip App")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf


# ----------------------------- UI helpers -----------------------------

def admin_gate():
    st.sidebar.subheader("Admin Sign-in")
    admin_key = st.sidebar.text_input("Admin password", type="password")
    expected = st.secrets.get("ADMIN_PASSWORD", None)
    if expected:
        return admin_key == expected
    # If no secret configured, allow any non-empty password
    return bool(admin_key)


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
        (emp_id, full_name, position, department, rate_type, base_rate),
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
            row.get("emp_id"), row.get("period_start"), row.get("period_end"),
            float(row.get("basic_pay", 0) or 0), float(row.get("overtime_pay", 0) or 0), float(row.get("allowances", 0) or 0),
            float(row.get("sss", 0) or 0), float(row.get("philhealth", 0) or 0), float(row.get("pagibig", 0) or 0),
            float(row.get("tax", 0) or 0), float(row.get("other_deductions", 0) or 0),
            row.get("notes")
        ),
    )
    conn.commit()
    conn.close()


def list_employees_df():
    conn = get_conn()
    df = pd.read_sql_query("SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name", conn)
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
    cur.execute("SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=?", (emp_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"emp_id": row[0], "full_name": row[1], "position": row[2], "department": row[3]}


# ----------------------------- Streamlit App -----------------------------

def main():
    st.set_page_config(page_title="Payslip Generator", page_icon="💸", layout="wide")
    init_db()

    st.title("💸 Payslip Generator & Data Encoder")
    st.caption("Store employee + payroll data in SQLite. Generate downloadable PDF payslips.")

    with st.sidebar:
        st.markdown("**Company Profile (appears on payslips)**")
        company_name = st.text_input("Company Name", value="Your Company Name")
        company_address = st.text_input("Company Address", value="City, Country")
        company_tin = st.text_input("Company TIN (optional)")
        st.divider()
        mode = st.radio("Mode", ["Employee Self-Service", "Admin"], index=0)

    if mode == "Admin":
        if not admin_gate():
            st.info("Enter admin password in the sidebar to continue. Configure ADMIN_PASSWORD in Streamlit secrets for production.")
            return

        tabs = st.tabs(["Employees", "Add Payroll", "All Payroll Records"])  # Admin tabs

        # --- Employees tab ---
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
                if st.button("Save Employee", use_container_width=True, type="primary"):
                    if emp_id and full_name:
                        upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                        st.success(f"Saved {full_name} ({emp_id}).")
                    else:
                        st.error("Employee ID and Full Name are required.")

            st.dataframe(list_employees_df(), use_container_width=True)

            st.markdown("**Bulk Upload (CSV)**")
            st.caption("Columns: emp_id, full_name, position, department, rate_type, base_rate")
            file = st.file_uploader("Upload employees.csv", type=["csv"])
            if file is not None:
                try:
                    df = pd.read_csv(file)
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
                    st.error(f"Failed to import: {e}")

        # --- Add Payroll tab ---
        with tabs[1]:
            st.subheader("Add Payroll Entry")
            emp_list = list_employees_df()
            emp_opts = [f"{r.full_name} ({r.emp_id})" for _, r in emp_list.iterrows()]
            selected = st.selectbox("Select Employee", options=["-"] + emp_opts)
            if selected != "-":
                selected_emp_id = selected.split("(")[-1].rstrip(")")
            else:
                selected_emp_id = None

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

            if st.button("Save Payroll Entry", type="primary", use_container_width=True):
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

            st.divider()
            st.markdown("**Bulk Upload (CSV)**")
            st.caption(
                "Columns: emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, sss, philhealth, pagibig, tax, other_deductions, notes"
            )
            file2 = st.file_uploader("Upload payroll.csv", type=["csv"])
            if file2 is not None:
                try:
                    df2 = pd.read_csv(file2)
                    required = {"emp_id", "period_start", "period_end"}
                    if not required.issubset(set(df2.columns)):
                        st.error("CSV must include emp_id, period_start, period_end.")
                    else:
                        for _, r in df2.iterrows():
                            insert_payroll({k: r.get(k) for k in df2.columns})
                        st.success(f"Imported {len(df2)} payroll rows.")
                except Exception as e:
                    st.error(f"Failed to import: {e}")

        # --- All Payroll Records tab ---
        with tabs[2]:
            st.subheader("All Payroll Records")
            df = list_payroll_df()
            st.dataframe(df, use_container_width=True)

    else:  # Employee Self-Service
        st.subheader("Employee Self-Service")
        emp_id = st.text_input("Enter your Employee ID")
        if emp_id:
            emp = get_employee(emp_id)
            if not emp:
                st.error("Employee ID not found.")
            else:
                df = list_payroll_df(emp_id)
                if df.empty:
                    st.info("No payroll records yet.")
                else:
                    # Let employee choose a period
                    df["period"] = df["period_start"] + " to " + df["period_end"]
                    period = st.selectbox("Select Pay Period", options=df["period"].tolist())
                    row = df[df["period"] == period].iloc[0].to_dict()

                    # Show summary
                    gross = float(row.get("basic_pay", 0) or 0) + float(row.get("overtime_pay", 0) or 0) + float(row.get("allowances", 0) or 0)
                    deductions = sum(
                        float(row.get(k, 0) or 0)
                        for k in ["sss", "philhealth", "pagibig", "tax", "other_deductions"]
                    )
                    net = gross - deductions

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Gross Pay", peso(gross))
                    col2.metric("Total Deductions", peso(deductions))
                    col3.metric("Net Pay", peso(net))

                    if st.button("Download PDF Payslip", type="primary"):
                        pdf_bytes = make_payslip_pdf(company_name, company_address, company_tin, row, emp)
                        filename = f"payslip_{emp_id}_{row.get('period_start')}_{row.get('period_end')}.pdf"
                        st.download_button(
                            label="Click to save PDF",
                            data=pdf_bytes,
                            file_name=filename,
                            mime="application/pdf",
                        )


if __name__ == "__main__":
    main()
