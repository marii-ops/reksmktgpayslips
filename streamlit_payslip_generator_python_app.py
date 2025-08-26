# streamlit_app.py
# REKS Amusement Com Inc – Marketing Department
# Streamlit + Supabase (Postgres) payslip system
# Features
# - Admin login (via `ADMIN_PASSWORD` in secrets) to manage Employees & Payroll
# - Employee Self-Service to view/download own payslips (by Employee ID)
# - Bulk upload (Excel/CSV) for Employees and Payroll
# - Downloadable Excel templates (employee_template.xlsx, payroll_template.xlsx)
# - Delete employees & payroll entries
# - Merge duplicate payroll rows (same emp_id + period_start + period_end)
# - PDF payslip generation (ReportLab)
# - Postgres tables auto-created with proper constraints + ON DELETE CASCADE
#
# Requirements (requirements.txt):
# streamlit
# psycopg2-binary
# pandas
# reportlab
# openpyxl

import io
import math
from datetime import date, datetime
from typing import Optional

import pandas as pd
import psycopg2
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ----------------------------- CONFIG -----------------------------
COMPANY_NAME = "REKS Amusement Com Inc"
COMPANY_DEPT = "Marketing Department"
COMPANY_ADDRESS = ""
COMPANY_TIN = ""

# ----------------------------- DB -----------------------------
@st.cache_resource(show_spinner=False)
def get_conn():
    """Connect to Supabase Postgres using Streamlit secrets.
    secrets.toml must contain:
    [postgres]
    host="...supabase.co"
    dbname="postgres"
    user="postgres"
    password="<YOUR_PASSWORD>"
    port="5432"
    """
    cfg = st.secrets["postgres"]
    return psycopg2.connect(
        host=cfg["host"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        port=str(cfg.get("port", "5432")),
        sslmode="require",
    )


def run_sql(sql: str, params: Optional[tuple] = None, fetch: bool = False):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


def init_db():
    # Employees
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            emp_id TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            position TEXT,
            department TEXT,
            rate_type TEXT,
            base_rate NUMERIC,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )
    # Payroll
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS payroll (
            id SERIAL PRIMARY KEY,
            emp_id TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            basic_pay NUMERIC DEFAULT 0,
            overtime_pay NUMERIC DEFAULT 0,
            allowances NUMERIC DEFAULT 0,
            bonus NUMERIC DEFAULT 0,
            sss NUMERIC DEFAULT 0,
            philhealth NUMERIC DEFAULT 0,
            pagibig NUMERIC DEFAULT 0,
            undertime NUMERIC DEFAULT 0,
            late NUMERIC DEFAULT 0,
            other_deductions NUMERIC DEFAULT 0,
            tax NUMERIC DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(emp_id, period_start, period_end)
        );
        """
    )


# ----------------------------- UTIL -----------------------------
def peso(x) -> str:
    try:
        return f"₱{float(x):,.2f}"
    except Exception:
        return "₱0.00"


def to_float(val) -> float:
    """Safe numeric coercion for uploads. Accepts '', None, strings, numbers."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    try:
        return float(s) if s else 0.0
    except Exception:
        return 0.0


# ----------------------------- CRUD -----------------------------
def upsert_employee(emp_id, full_name, position, department, rate_type, base_rate):
    run_sql(
        """
        INSERT INTO employees (emp_id, full_name, position, department, rate_type, base_rate)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (emp_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            position = EXCLUDED.position,
            department = EXCLUDED.department,
            rate_type = EXCLUDED.rate_type,
            base_rate = EXCLUDED.base_rate;
        """,
        (emp_id, full_name, position, department, rate_type, to_float(base_rate)),
    )


def delete_employee(emp_id: str):
    run_sql("DELETE FROM employees WHERE emp_id=%s", (emp_id,))


def list_employees_df() -> pd.DataFrame:
    rows = run_sql(
        "SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name",
        fetch=True,
    )
    return pd.DataFrame(rows, columns=["emp_id", "full_name", "position", "department", "rate_type", "base_rate", "created_at"]) if rows else pd.DataFrame(columns=["emp_id", "full_name", "position", "department", "rate_type", "base_rate", "created_at"])


def insert_or_update_payroll(row: dict):
    # Uses upsert based on UNIQUE(emp_id, period_start, period_end)
    run_sql(
        """
        INSERT INTO payroll (
            emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, bonus,
            sss, philhealth, pagibig, undertime, late, other_deductions, tax, notes
        ) VALUES (
            %(emp_id)s, %(period_start)s, %(period_end)s, %(basic_pay)s, %(overtime_pay)s, %(allowances)s, %(bonus)s,
            %(sss)s, %(philhealth)s, %(pagibig)s, %(undertime)s, %(late)s, %(other_deductions)s, %(tax)s, %(notes)s
        )
        ON CONFLICT (emp_id, period_start, period_end) DO UPDATE SET
            basic_pay = EXCLUDED.basic_pay,
            overtime_pay = EXCLUDED.overtime_pay,
            allowances = EXCLUDED.allowances,
            bonus = EXCLUDED.bonus,
            sss = EXCLUDED.sss,
            philhealth = EXCLUDED.philhealth,
            pagibig = EXCLUDED.pagibig,
            undertime = EXCLUDED.undertime,
            late = EXCLUDED.late,
            other_deductions = EXCLUDED.other_deductions,
            tax = EXCLUDED.tax,
            notes = EXCLUDED.notes;
        """,
        {
            "emp_id": row.get("emp_id"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "basic_pay": to_float(row.get("basic_pay")),
            "overtime_pay": to_float(row.get("overtime_pay")),
            "allowances": to_float(row.get("allowances")),
            "bonus": to_float(row.get("bonus")),
            "sss": to_float(row.get("sss")),
            "philhealth": to_float(row.get("philhealth")),
            "pagibig": to_float(row.get("pagibig")),
            "undertime": to_float(row.get("undertime")),
            "late": to_float(row.get("late")),
            "other_deductions": to_float(row.get("other_deductions")),
            "tax": to_float(row.get("tax")),
            "notes": (row.get("notes") or None),
        },
    )


def delete_payroll(id_: int):
    run_sql("DELETE FROM payroll WHERE id=%s", (id_,))


def list_payroll_df(emp_id: Optional[str] = None) -> pd.DataFrame:
    if emp_id:
        rows = run_sql(
            "SELECT * FROM payroll WHERE emp_id=%s ORDER BY period_start DESC", (emp_id,), fetch=True
        )
    else:
        rows = run_sql("SELECT * FROM payroll ORDER BY created_at DESC", fetch=True)
    cols = [
        "id","emp_id","period_start","period_end","basic_pay","overtime_pay","allowances","bonus",
        "sss","philhealth","pagibig","undertime","late","other_deductions","tax","notes","created_at"
    ]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def get_employee(emp_id: str) -> Optional[dict]:
    rows = run_sql(
        "SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=%s",
        (emp_id,),
        fetch=True,
    )
    if not rows:
        return None
    r = rows[0]
    return {"emp_id": r[0], "full_name": r[1], "position": r[2], "department": r[3]}


def merge_duplicate_payroll():
    """Keep latest id for duplicates on (emp_id, period_start, period_end); delete the rest."""
    dups = run_sql(
        """
        WITH ranked AS (
          SELECT id, emp_id, period_start, period_end,
                 ROW_NUMBER() OVER (PARTITION BY emp_id, period_start, period_end ORDER BY id DESC) AS rn
          FROM payroll
        )
        DELETE FROM payroll p
        USING ranked r
        WHERE p.id = r.id AND r.rn > 1
        RETURNING p.id;
        """,
        fetch=True,
    )
    return len(dups or [])


# ----------------------------- PDF -----------------------------
def make_payslip_pdf(payroll_row: dict, employee_row: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    margin = 18 * mm
    x0 = margin
    y = height - margin

    def draw_header():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0, y, COMPANY_NAME)
        y -= 14
        c.setFont("Helvetica", 10)
        if COMPANY_DEPT:
            c.drawString(x0, y, COMPANY_DEPT)
            y -= 12
        if COMPANY_ADDRESS:
            c.drawString(x0, y, COMPANY_ADDRESS)
            y -= 12
        if COMPANY_TIN:
            c.drawString(x0, y, f"TIN: {COMPANY_TIN}")
            y -= 14
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

    period_start = str(payroll_row.get("period_start") or "")
    period_end = str(payroll_row.get("period_end") or "")

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
        ("Basic Pay", to_float(payroll_row.get("basic_pay"))),
        ("Overtime Pay", to_float(payroll_row.get("overtime_pay"))),
        ("Allowances", to_float(payroll_row.get("allowances"))),
        ("Bonus", to_float(payroll_row.get("bonus"))),
    ]
    deductions = [
        ("SSS", to_float(payroll_row.get("sss"))),
        ("PhilHealth", to_float(payroll_row.get("philhealth"))),
        ("Pag-IBIG", to_float(payroll_row.get("pagibig"))),
        ("Undertime", to_float(payroll_row.get("undertime"))),
        ("Late", to_float(payroll_row.get("late"))),
        ("Other Deductions", to_float(payroll_row.get("other_deductions"))),
        ("Withholding Tax", to_float(payroll_row.get("tax"))),
    ]

    gross = sum(v for _, v in earnings)
    total_deductions = sum(v for _, v in deductions)
    net = gross - total_deductions

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "EARNINGS")
    c.drawString(width / 2, y, "DEDUCTIONS")
    y -= 12

    c.setFont("Helvetica", 10)
    y_left = y
    for label, val in earnings:
        c.drawString(x0, y_left, label)
        c.drawRightString(width / 2 - 10, y_left, peso(val))
        y_left -= 12

    y_right = y
    for label, val in deductions:
        c.drawString(width / 2 + 10, y_right, label)
        c.drawRightString(width - margin, y_right, peso(val))
        y_right -= 12

    y = min(y_left, y_right) - 10
    c.line(x0, y, width - margin, y)
    y -= 14

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "Gross Pay:")
    c.drawRightString(width / 2 - 10, y, peso(gross))
    c.drawString(width / 2 + 10, y, "Total Deductions:")
    c.drawRightString(width - margin, y, peso(total_deductions))

    y -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, y, "NET PAY:")
    c.drawRightString(width - margin, y, peso(net))

    y -= 20
    notes = str(payroll_row.get("notes") or "").strip()
    if notes:
        c.setFont("Helvetica", 9)
        c.drawString(x0, y, f"Notes: {notes}")
        y -= 12

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} via REKS Payslip App")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf


# ----------------------------- UI -----------------------------
def admin_gate() -> bool:
    st.sidebar.subheader("Admin sign-in")
    pwd = st.sidebar.text_input("Admin password", type="password")
    expected = st.secrets.get("ADMIN_PASSWORD")
    if expected:
        return pwd == expected
    return bool(pwd)


def download_employee_template():
    cols = ["emp_id","full_name","position","department","rate_type","base_rate"]
    df = pd.DataFrame(columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="employees")
    buf.seek(0)
    return buf


def download_payroll_template():
    cols = [
        "emp_id","period_start","period_end","basic_pay","overtime_pay","allowances","bonus",
        "sss","philhealth","pagibig","undertime","late","other_deductions","tax","notes"
    ]
    df = pd.DataFrame(columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="payroll")
    buf.seek(0)
    return buf


def import_employees_from_df(df: pd.DataFrame) -> tuple[int, list[str]]:
    required = {"emp_id", "full_name"}
    msgs = []
    if not required.issubset(set(df.columns)):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"Employees sheet missing required columns: {missing}")
    count = 0
    for _, r in df.iterrows():
        emp_id = str(r.get("emp_id")).strip()
        full_name = str(r.get("full_name")).strip()
        position = str(r.get("position") or "")
        department = str(r.get("department") or "")
        rate_type = str(r.get("rate_type") or "")
        base_rate = to_float(r.get("base_rate"))
        if not emp_id or not full_name:
            msgs.append("Skipped a row (missing emp_id or full_name)")
            continue
        upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
        count += 1
    return count, msgs


def import_payroll_from_df(df: pd.DataFrame) -> tuple[int, list[str]]:
    required = {"emp_id", "period_start", "period_end"}
    msgs = []
    if not required.issubset(set(df.columns)):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"Payroll sheet missing required columns: {missing}")
    count = 0
    for _, r in df.iterrows():
        try:
            row = {
                "emp_id": str(r.get("emp_id")).strip(),
                "period_start": pd.to_datetime(r.get("period_start")).date() if pd.notna(r.get("period_start")) else None,
                "period_end": pd.to_datetime(r.get("period_end")).date() if pd.notna(r.get("period_end")) else None,
                "basic_pay": to_float(r.get("basic_pay")),
                "overtime_pay": to_float(r.get("overtime_pay")),
                "allowances": to_float(r.get("allowances")),
                "bonus": to_float(r.get("bonus")),
                "sss": to_float(r.get("sss")),
                "philhealth": to_float(r.get("philhealth")),
                "pagibig": to_float(r.get("pagibig")),
                "undertime": to_float(r.get("undertime")),
                "late": to_float(r.get("late")),
                "other_deductions": to_float(r.get("other_deductions")),
                "tax": to_float(r.get("tax")),
                "notes": str(r.get("notes") or ""),
            }
            if not row["emp_id"] or not row["period_start"] or not row["period_end"]:
                msgs.append("Skipped a row (missing emp_id/period_start/period_end)")
                continue
            insert_or_update_payroll(row)
            count += 1
        except Exception as e:
            msgs.append(f"Row error: {e}")
    return count, msgs


# ----------------------------- APP -----------------------------
def main():
    st.set_page_config(page_title="REKS Payslips", page_icon="💸", layout="wide")
    init_db()

    st.title("💸 REKS Payslips – Marketing Department")
    st.caption("Supabase-backed payroll with admin + self-service PDF payslips.")

    mode = st.sidebar.radio("Mode", ["Employee Self-Service", "Admin"], index=0)

    if mode == "Admin":
        if not admin_gate():
            st.info("Enter admin password in the sidebar to continue. (Configure ADMIN_PASSWORD in Secrets).")
            return

        tabs = st.tabs(["Employees", "Payroll", "All Payroll Records", "Utilities"])

        # ---------------- Employees Tab ----------------
        with tabs[0]:
            st.subheader("Employees")

            with st.expander("➕ Add / Update Employee"):
                c1, c2 = st.columns(2)
                with c1:
                    emp_id = st.text_input("Employee ID *")
                    full_name = st.text_input("Full Name *")
                    position = st.text_input("Position")
                with c2:
                    department = st.text_input("Department", value=COMPANY_DEPT)
                    rate_type = st.selectbox("Rate Type", ["", "monthly", "daily", "hourly"], index=0)
                    base_rate = st.number_input("Base Rate", min_value=0.0, step=0.01)
                if st.button("Save Employee", type="primary"):
                    if emp_id and full_name:
                        upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                        st.success(f"Saved {full_name} ({emp_id}).")
                    else:
                        st.error("Employee ID and Full Name are required.")

            df_emp = list_employees_df()
            st.dataframe(df_emp, use_container_width=True)

            st.divider()
            st.markdown("**Bulk Upload – Employees**")
            st.caption("Accepted: .xlsx or .csv | Required columns: emp_id, full_name")
            colT, colU = st.columns([1, 1])
            with colT:
                if st.button("⬇️ Download employee_template.xlsx"):
                    st.download_button("Save template", data=download_employee_template(), file_name="employee_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with colU:
                emp_file = st.file_uploader("Upload Employees file", type=["xlsx", "csv"], key="emp_upload")
                if emp_file is not None:
                    try:
                        if emp_file.name.lower().endswith(".csv"):
                            dfu = pd.read_csv(emp_file)
                        else:
                            dfu = pd.read_excel(emp_file)
                        n, msgs = import_employees_from_df(dfu)
                        st.success(f"Imported/updated {n} employees.")
                        if msgs:
                            with st.expander("Import notes"):
                                for m in msgs:
                                    st.write("- ", m)
                    except Exception as e:
                        st.error(f"Failed to import employees: {e}")

            st.divider()
            st.markdown("**Delete Employee**")
            if not df_emp.empty:
                del_emp = st.selectbox("Choose Employee to delete", ["-"] + df_emp["emp_id"].tolist())
                if st.button("Delete Selected Employee", type="secondary"):
                    if del_emp != "-":
                        delete_employee(del_emp)
                        st.success(f"Deleted employee {del_emp} (and their payroll records).")
                    else:
                        st.warning("Select an employee.")

        # ---------------- Payroll Tab ----------------
        with tabs[1]:
            st.subheader("Add or Update Payroll Entry")
            df_emp2 = list_employees_df()
            emp_opts = [f"{r.full_name} ({r.emp_id})" for _, r in df_emp2.iterrows()]
            picked = st.selectbox("Employee", options=["-"] + emp_opts)
            selected_emp_id = picked.split("(")[-1].rstrip(")") if picked != "-" else None

            c1, c2, c3 = st.columns(3)
            with c1:
                period_start = st.date_input("Period Start", value=date.today())
                basic_pay = st.number_input("Basic Pay", min_value=0.0, step=0.01)
                overtime_pay = st.number_input("Overtime Pay", min_value=0.0, step=0.01)
                allowances = st.number_input("Allowances", min_value=0.0, step=0.01)
                bonus = st.number_input("Bonus", min_value=0.0, step=0.01)
            with c2:
                sss = st.number_input("SSS", min_value=0.0, step=0.01)
                philhealth = st.number_input("PhilHealth", min_value=0.0, step=0.01)
                pagibig = st.number_input("Pag-IBIG", min_value=0.0, step=0.01)
                undertime = st.number_input("Undertime", min_value=0.0, step=0.01)
                late = st.number_input("Late", min_value=0.0, step=0.01)
            with c3:
                other_deductions = st.number_input("Other Deductions", min_value=0.0, step=0.01)
                tax = st.number_input("Withholding Tax", min_value=0.0, step=0.01)
                period_end = st.date_input("Period End", value=date.today())
                notes = st.text_input("Notes (optional)")

            if st.button("Save Payroll", type="primary"):
                if not selected_emp_id:
                    st.error("Select an employee.")
                else:
                    insert_or_update_payroll(
                        {
                            "emp_id": selected_emp_id,
                            "period_start": period_start,
                            "period_end": period_end,
                            "basic_pay": basic_pay,
                            "overtime_pay": overtime_pay,
                            "allowances": allowances,
                            "bonus": bonus,
                            "sss": sss,
                            "philhealth": philhealth,
                            "pagibig": pagibig,
                            "undertime": undertime,
                            "late": late,
                            "other_deductions": other_deductions,
                            "tax": tax,
                            "notes": notes,
                        }
                    )
                    st.success("Payroll saved.")

            st.divider()
            st.markdown("**Bulk Upload – Payroll**")
            st.caption("Accepted: .xlsx or .csv | Required columns: emp_id, period_start, period_end")
            colPT, colPU = st.columns([1, 1])
            with colPT:
                if st.button("⬇️ Download payroll_template.xlsx"):
                    st.download_button(
                        "Save template",
                        data=download_payroll_template(),
                        file_name="payroll_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with colPU:
                pay_file = st.file_uploader("Upload Payroll file", type=["xlsx", "csv"], key="pay_upload")
                if pay_file is not None:
                    try:
                        if pay_file.name.lower().endswith(".csv"):
                            dfp = pd.read_csv(pay_file)
                        else:
                            dfp = pd.read_excel(pay_file)
                        n, msgs = import_payroll_from_df(dfp)
                        st.success(f"Imported/updated {n} payroll rows.")
                        if msgs:
                            with st.expander("Import notes"):
                                for m in msgs:
                                    st.write("- ", m)
                    except Exception as e:
                        st.error(f"Failed to import payroll: {e}")

        # ---------------- All Payroll Records Tab ----------------
        with tabs[2]:
            st.subheader("All Payroll Records")
            df_all = list_payroll_df()
            st.dataframe(df_all, use_container_width=True)
            if not df_all.empty:
                del_id = st.number_input("Delete payroll by ID", min_value=0, step=1)
                if st.button("Delete Payroll Row"):
                    if del_id > 0:
                        delete_payroll(int(del_id))
                        st.success(f"Deleted payroll id {int(del_id)}")
                    else:
                        st.warning("Enter a valid id.")

        # ---------------- Utilities Tab ----------------
        with tabs[3]:
            st.subheader("Utilities")
            if st.button("Merge duplicate payroll rows"):
                removed = merge_duplicate_payroll()
                st.success(f"Removed {removed} duplicate rows (kept latest per (emp_id, period)).")

    else:
        # ---------------- Employee Self-Service ----------------
        st.subheader("Employee Self-Service")
        emp_id = st.text_input("Enter your Employee ID")
        if emp_id:
            emp = get_employee(emp_id)
            if not emp:
                st.error("Employee ID not found.")
            else:
                df = list_payroll_df(emp_id)
                if df.empty:
                    st.info("No payroll records found.")
                else:
                    df["period"] = df["period_start"].astype(str) + " to " + df["period_end"].astype(str)
                    period = st.selectbox("Select Pay Period", options=df["period"].tolist())
                    row = df[df["period"] == period].iloc[0].to_dict()

                    gross = sum(
                        to_float(row.get(k)) for k in ["basic_pay", "overtime_pay", "allowances", "bonus"]
                    )
                    deductions = sum(
                        to_float(row.get(k))
                        for k in ["sss", "philhealth", "pagibig", "undertime", "late", "other_deductions", "tax"]
                    )
                    net = gross - deductions

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Gross Pay", peso(gross))
                    c2.metric("Deductions", peso(deductions))
                    c3.metric("Net Pay", peso(net))

                    pdf_bytes = make_payslip_pdf(row, emp)
                    fname = f"payslip_{emp_id}_{row.get('period_start')}_{row.get('period_end')}.pdf"
                    st.download_button("📥 Download PDF Payslip", data=pdf_bytes, file_name=fname, mime="application/pdf")


if __name__ == "__main__":
    main()
# streamlit_app.py
# REKS Amusement Com Inc – Marketing Department
# Streamlit + Supabase (Postgres) payslip system
# Features
# - Admin login (via `ADMIN_PASSWORD` in secrets) to manage Employees & Payroll
# - Employee Self-Service to view/download own payslips (by Employee ID)
# - Bulk upload (Excel/CSV) for Employees and Payroll
# - Downloadable Excel templates (employee_template.xlsx, payroll_template.xlsx)
# - Delete employees & payroll entries
# - Merge duplicate payroll rows (same emp_id + period_start + period_end)
# - PDF payslip generation (ReportLab)
# - Postgres tables auto-created with proper constraints + ON DELETE CASCADE
#
# Requirements (requirements.txt):
# streamlit
# psycopg2-binary
# pandas
# reportlab
# openpyxl

import io
import math
from datetime import date, datetime
from typing import Optional

import pandas as pd
import psycopg2
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ----------------------------- CONFIG -----------------------------
COMPANY_NAME = "REKS Amusement Com Inc"
COMPANY_DEPT = "Marketing Department"
COMPANY_ADDRESS = ""
COMPANY_TIN = ""

# ----------------------------- DB -----------------------------
@st.cache_resource(show_spinner=False)
def get_conn():
    """Connect to Supabase Postgres using Streamlit secrets.
    secrets.toml must contain:
    [postgres]
    host="...supabase.co"
    dbname="postgres"
    user="postgres"
    password="<YOUR_PASSWORD>"
    port="5432"
    """
    cfg = st.secrets["postgres"]
    return psycopg2.connect(
        host=cfg["host"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        port=str(cfg.get("port", "5432")),
        sslmode="require",
    )


def run_sql(sql: str, params: Optional[tuple] = None, fetch: bool = False):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if fetch:
                return cur.fetchall()
    return None


def init_db():
    # Employees
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            emp_id TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            position TEXT,
            department TEXT,
            rate_type TEXT,
            base_rate NUMERIC,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )
    # Payroll
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS payroll (
            id SERIAL PRIMARY KEY,
            emp_id TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            basic_pay NUMERIC DEFAULT 0,
            overtime_pay NUMERIC DEFAULT 0,
            allowances NUMERIC DEFAULT 0,
            bonus NUMERIC DEFAULT 0,
            sss NUMERIC DEFAULT 0,
            philhealth NUMERIC DEFAULT 0,
            pagibig NUMERIC DEFAULT 0,
            undertime NUMERIC DEFAULT 0,
            late NUMERIC DEFAULT 0,
            other_deductions NUMERIC DEFAULT 0,
            tax NUMERIC DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(emp_id, period_start, period_end)
        );
        """
    )


# ----------------------------- UTIL -----------------------------
def peso(x) -> str:
    try:
        return f"₱{float(x):,.2f}"
    except Exception:
        return "₱0.00"


def to_float(val) -> float:
    """Safe numeric coercion for uploads. Accepts '', None, strings, numbers."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    try:
        return float(s) if s else 0.0
    except Exception:
        return 0.0


# ----------------------------- CRUD -----------------------------
def upsert_employee(emp_id, full_name, position, department, rate_type, base_rate):
    run_sql(
        """
        INSERT INTO employees (emp_id, full_name, position, department, rate_type, base_rate)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (emp_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            position = EXCLUDED.position,
            department = EXCLUDED.department,
            rate_type = EXCLUDED.rate_type,
            base_rate = EXCLUDED.base_rate;
        """,
        (emp_id, full_name, position, department, rate_type, to_float(base_rate)),
    )


def delete_employee(emp_id: str):
    run_sql("DELETE FROM employees WHERE emp_id=%s", (emp_id,))


def list_employees_df() -> pd.DataFrame:
    rows = run_sql(
        "SELECT emp_id, full_name, position, department, rate_type, base_rate, created_at FROM employees ORDER BY full_name",
        fetch=True,
    )
    return pd.DataFrame(rows, columns=["emp_id", "full_name", "position", "department", "rate_type", "base_rate", "created_at"]) if rows else pd.DataFrame(columns=["emp_id", "full_name", "position", "department", "rate_type", "base_rate", "created_at"])


def insert_or_update_payroll(row: dict):
    # Uses upsert based on UNIQUE(emp_id, period_start, period_end)
    run_sql(
        """
        INSERT INTO payroll (
            emp_id, period_start, period_end, basic_pay, overtime_pay, allowances, bonus,
            sss, philhealth, pagibig, undertime, late, other_deductions, tax, notes
        ) VALUES (
            %(emp_id)s, %(period_start)s, %(period_end)s, %(basic_pay)s, %(overtime_pay)s, %(allowances)s, %(bonus)s,
            %(sss)s, %(philhealth)s, %(pagibig)s, %(undertime)s, %(late)s, %(other_deductions)s, %(tax)s, %(notes)s
        )
        ON CONFLICT (emp_id, period_start, period_end) DO UPDATE SET
            basic_pay = EXCLUDED.basic_pay,
            overtime_pay = EXCLUDED.overtime_pay,
            allowances = EXCLUDED.allowances,
            bonus = EXCLUDED.bonus,
            sss = EXCLUDED.sss,
            philhealth = EXCLUDED.philhealth,
            pagibig = EXCLUDED.pagibig,
            undertime = EXCLUDED.undertime,
            late = EXCLUDED.late,
            other_deductions = EXCLUDED.other_deductions,
            tax = EXCLUDED.tax,
            notes = EXCLUDED.notes;
        """,
        {
            "emp_id": row.get("emp_id"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "basic_pay": to_float(row.get("basic_pay")),
            "overtime_pay": to_float(row.get("overtime_pay")),
            "allowances": to_float(row.get("allowances")),
            "bonus": to_float(row.get("bonus")),
            "sss": to_float(row.get("sss")),
            "philhealth": to_float(row.get("philhealth")),
            "pagibig": to_float(row.get("pagibig")),
            "undertime": to_float(row.get("undertime")),
            "late": to_float(row.get("late")),
            "other_deductions": to_float(row.get("other_deductions")),
            "tax": to_float(row.get("tax")),
            "notes": (row.get("notes") or None),
        },
    )


def delete_payroll(id_: int):
    run_sql("DELETE FROM payroll WHERE id=%s", (id_,))


def list_payroll_df(emp_id: Optional[str] = None) -> pd.DataFrame:
    if emp_id:
        rows = run_sql(
            "SELECT * FROM payroll WHERE emp_id=%s ORDER BY period_start DESC", (emp_id,), fetch=True
        )
    else:
        rows = run_sql("SELECT * FROM payroll ORDER BY created_at DESC", fetch=True)
    cols = [
        "id","emp_id","period_start","period_end","basic_pay","overtime_pay","allowances","bonus",
        "sss","philhealth","pagibig","undertime","late","other_deductions","tax","notes","created_at"
    ]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def get_employee(emp_id: str) -> Optional[dict]:
    rows = run_sql(
        "SELECT emp_id, full_name, position, department FROM employees WHERE emp_id=%s",
        (emp_id,),
        fetch=True,
    )
    if not rows:
        return None
    r = rows[0]
    return {"emp_id": r[0], "full_name": r[1], "position": r[2], "department": r[3]}


def merge_duplicate_payroll():
    """Keep latest id for duplicates on (emp_id, period_start, period_end); delete the rest."""
    dups = run_sql(
        """
        WITH ranked AS (
          SELECT id, emp_id, period_start, period_end,
                 ROW_NUMBER() OVER (PARTITION BY emp_id, period_start, period_end ORDER BY id DESC) AS rn
          FROM payroll
        )
        DELETE FROM payroll p
        USING ranked r
        WHERE p.id = r.id AND r.rn > 1
        RETURNING p.id;
        """,
        fetch=True,
    )
    return len(dups or [])


# ----------------------------- PDF -----------------------------
def make_payslip_pdf(payroll_row: dict, employee_row: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    margin = 18 * mm
    x0 = margin
    y = height - margin

    def draw_header():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0, y, COMPANY_NAME)
        y -= 14
        c.setFont("Helvetica", 10)
        if COMPANY_DEPT:
            c.drawString(x0, y, COMPANY_DEPT)
            y -= 12
        if COMPANY_ADDRESS:
            c.drawString(x0, y, COMPANY_ADDRESS)
            y -= 12
        if COMPANY_TIN:
            c.drawString(x0, y, f"TIN: {COMPANY_TIN}")
            y -= 14
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

    period_start = str(payroll_row.get("period_start") or "")
    period_end = str(payroll_row.get("period_end") or "")

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
        ("Basic Pay", to_float(payroll_row.get("basic_pay"))),
        ("Overtime Pay", to_float(payroll_row.get("overtime_pay"))),
        ("Allowances", to_float(payroll_row.get("allowances"))),
        ("Bonus", to_float(payroll_row.get("bonus"))),
    ]
    deductions = [
        ("SSS", to_float(payroll_row.get("sss"))),
        ("PhilHealth", to_float(payroll_row.get("philhealth"))),
        ("Pag-IBIG", to_float(payroll_row.get("pagibig"))),
        ("Undertime", to_float(payroll_row.get("undertime"))),
        ("Late", to_float(payroll_row.get("late"))),
        ("Other Deductions", to_float(payroll_row.get("other_deductions"))),
        ("Withholding Tax", to_float(payroll_row.get("tax"))),
    ]

    gross = sum(v for _, v in earnings)
    total_deductions = sum(v for _, v in deductions)
    net = gross - total_deductions

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "EARNINGS")
    c.drawString(width / 2, y, "DEDUCTIONS")
    y -= 12

    c.setFont("Helvetica", 10)
    y_left = y
    for label, val in earnings:
        c.drawString(x0, y_left, label)
        c.drawRightString(width / 2 - 10, y_left, peso(val))
        y_left -= 12

    y_right = y
    for label, val in deductions:
        c.drawString(width / 2 + 10, y_right, label)
        c.drawRightString(width - margin, y_right, peso(val))
        y_right -= 12

    y = min(y_left, y_right) - 10
    c.line(x0, y, width - margin, y)
    y -= 14

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0, y, "Gross Pay:")
    c.drawRightString(width / 2 - 10, y, peso(gross))
    c.drawString(width / 2 + 10, y, "Total Deductions:")
    c.drawRightString(width - margin, y, peso(total_deductions))

    y -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, y, "NET PAY:")
    c.drawRightString(width - margin, y, peso(net))

    y -= 20
    notes = str(payroll_row.get("notes") or "").strip()
    if notes:
        c.setFont("Helvetica", 9)
        c.drawString(x0, y, f"Notes: {notes}")
        y -= 12

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(x0, 12 * mm, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} via REKS Payslip App")

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return pdf


# ----------------------------- UI -----------------------------
def admin_gate() -> bool:
    st.sidebar.subheader("Admin sign-in")
    pwd = st.sidebar.text_input("Admin password", type="password")
    expected = st.secrets.get("ADMIN_PASSWORD")
    if expected:
        return pwd == expected
    return bool(pwd)


def download_employee_template():
    cols = ["emp_id","full_name","position","department","rate_type","base_rate"]
    df = pd.DataFrame(columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="employees")
    buf.seek(0)
    return buf


def download_payroll_template():
    cols = [
        "emp_id","period_start","period_end","basic_pay","overtime_pay","allowances","bonus",
        "sss","philhealth","pagibig","undertime","late","other_deductions","tax","notes"
    ]
    df = pd.DataFrame(columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="payroll")
    buf.seek(0)
    return buf


def import_employees_from_df(df: pd.DataFrame) -> tuple[int, list[str]]:
    required = {"emp_id", "full_name"}
    msgs = []
    if not required.issubset(set(df.columns)):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"Employees sheet missing required columns: {missing}")
    count = 0
    for _, r in df.iterrows():
        emp_id = str(r.get("emp_id")).strip()
        full_name = str(r.get("full_name")).strip()
        position = str(r.get("position") or "")
        department = str(r.get("department") or "")
        rate_type = str(r.get("rate_type") or "")
        base_rate = to_float(r.get("base_rate"))
        if not emp_id or not full_name:
            msgs.append("Skipped a row (missing emp_id or full_name)")
            continue
        upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
        count += 1
    return count, msgs


def import_payroll_from_df(df: pd.DataFrame) -> tuple[int, list[str]]:
    required = {"emp_id", "period_start", "period_end"}
    msgs = []
    if not required.issubset(set(df.columns)):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"Payroll sheet missing required columns: {missing}")
    count = 0
    for _, r in df.iterrows():
        try:
            row = {
                "emp_id": str(r.get("emp_id")).strip(),
                "period_start": pd.to_datetime(r.get("period_start")).date() if pd.notna(r.get("period_start")) else None,
                "period_end": pd.to_datetime(r.get("period_end")).date() if pd.notna(r.get("period_end")) else None,
                "basic_pay": to_float(r.get("basic_pay")),
                "overtime_pay": to_float(r.get("overtime_pay")),
                "allowances": to_float(r.get("allowances")),
                "bonus": to_float(r.get("bonus")),
                "sss": to_float(r.get("sss")),
                "philhealth": to_float(r.get("philhealth")),
                "pagibig": to_float(r.get("pagibig")),
                "undertime": to_float(r.get("undertime")),
                "late": to_float(r.get("late")),
                "other_deductions": to_float(r.get("other_deductions")),
                "tax": to_float(r.get("tax")),
                "notes": str(r.get("notes") or ""),
            }
            if not row["emp_id"] or not row["period_start"] or not row["period_end"]:
                msgs.append("Skipped a row (missing emp_id/period_start/period_end)")
                continue
            insert_or_update_payroll(row)
            count += 1
        except Exception as e:
            msgs.append(f"Row error: {e}")
    return count, msgs


# ----------------------------- APP -----------------------------
def main():
    st.set_page_config(page_title="REKS Payslips", page_icon="💸", layout="wide")
    init_db()

    st.title("💸 REKS Payslips – Marketing Department")
    st.caption("Supabase-backed payroll with admin + self-service PDF payslips.")

    mode = st.sidebar.radio("Mode", ["Employee Self-Service", "Admin"], index=0)

    if mode == "Admin":
        if not admin_gate():
            st.info("Enter admin password in the sidebar to continue. (Configure ADMIN_PASSWORD in Secrets).")
            return

        tabs = st.tabs(["Employees", "Payroll", "All Payroll Records", "Utilities"])

        # ---------------- Employees Tab ----------------
        with tabs[0]:
            st.subheader("Employees")

            with st.expander("➕ Add / Update Employee"):
                c1, c2 = st.columns(2)
                with c1:
                    emp_id = st.text_input("Employee ID *")
                    full_name = st.text_input("Full Name *")
                    position = st.text_input("Position")
                with c2:
                    department = st.text_input("Department", value=COMPANY_DEPT)
                    rate_type = st.selectbox("Rate Type", ["", "monthly", "daily", "hourly"], index=0)
                    base_rate = st.number_input("Base Rate", min_value=0.0, step=0.01)
                if st.button("Save Employee", type="primary"):
                    if emp_id and full_name:
                        upsert_employee(emp_id, full_name, position, department, rate_type, base_rate)
                        st.success(f"Saved {full_name} ({emp_id}).")
                    else:
                        st.error("Employee ID and Full Name are required.")

            df_emp = list_employees_df()
            st.dataframe(df_emp, use_container_width=True)

            st.divider()
            st.markdown("**Bulk Upload – Employees**")
            st.caption("Accepted: .xlsx or .csv | Required columns: emp_id, full_name")
            colT, colU = st.columns([1, 1])
            with colT:
                if st.button("⬇️ Download employee_template.xlsx"):
                    st.download_button("Save template", data=download_employee_template(), file_name="employee_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with colU:
                emp_file = st.file_uploader("Upload Employees file", type=["xlsx", "csv"], key="emp_upload")
                if emp_file is not None:
                    try:
                        if emp_file.name.lower().endswith(".csv"):
                            dfu = pd.read_csv(emp_file)
                        else:
                            dfu = pd.read_excel(emp_file)
                        n, msgs = import_employees_from_df(dfu)
                        st.success(f"Imported/updated {n} employees.")
                        if msgs:
                            with st.expander("Import notes"):
                                for m in msgs:
                                    st.write("- ", m)
                    except Exception as e:
                        st.error(f"Failed to import employees: {e}")

            st.divider()
            st.markdown("**Delete Employee**")
            if not df_emp.empty:
                del_emp = st.selectbox("Choose Employee to delete", ["-"] + df_emp["emp_id"].tolist())
                if st.button("Delete Selected Employee", type="secondary"):
                    if del_emp != "-":
                        delete_employee(del_emp)
                        st.success(f"Deleted employee {del_emp} (and their payroll records).")
                    else:
                        st.warning("Select an employee.")

        # ---------------- Payroll Tab ----------------
        with tabs[1]:
            st.subheader("Add or Update Payroll Entry")
            df_emp2 = list_employees_df()
            emp_opts = [f"{r.full_name} ({r.emp_id})" for _, r in df_emp2.iterrows()]
            picked = st.selectbox("Employee", options=["-"] + emp_opts)
            selected_emp_id = picked.split("(")[-1].rstrip(")") if picked != "-" else None

            c1, c2, c3 = st.columns(3)
            with c1:
                period_start = st.date_input("Period Start", value=date.today())
                basic_pay = st.number_input("Basic Pay", min_value=0.0, step=0.01)
                overtime_pay = st.number_input("Overtime Pay", min_value=0.0, step=0.01)
                allowances = st.number_input("Allowances", min_value=0.0, step=0.01)
                bonus = st.number_input("Bonus", min_value=0.0, step=0.01)
            with c2:
                sss = st.number_input("SSS", min_value=0.0, step=0.01)
                philhealth = st.number_input("PhilHealth", min_value=0.0, step=0.01)
                pagibig = st.number_input("Pag-IBIG", min_value=0.0, step=0.01)
                undertime = st.number_input("Undertime", min_value=0.0, step=0.01)
                late = st.number_input("Late", min_value=0.0, step=0.01)
            with c3:
                other_deductions = st.number_input("Other Deductions", min_value=0.0, step=0.01)
                tax = st.number_input("Withholding Tax", min_value=0.0, step=0.01)
                period_end = st.date_input("Period End", value=date.today())
                notes = st.text_input("Notes (optional)")

            if st.button("Save Payroll", type="primary"):
                if not selected_emp_id:
                    st.error("Select an employee.")
                else:
                    insert_or_update_payroll(
                        {
                            "emp_id": selected_emp_id,
                            "period_start": period_start,
                            "period_end": period_end,
                            "basic_pay": basic_pay,
                            "overtime_pay": overtime_pay,
                            "allowances": allowances,
                            "bonus": bonus,
                            "sss": sss,
                            "philhealth": philhealth,
                            "pagibig": pagibig,
                            "undertime": undertime,
                            "late": late,
                            "other_deductions": other_deductions,
                            "tax": tax,
                            "notes": notes,
                        }
                    )
                    st.success("Payroll saved.")

            st.divider()
            st.markdown("**Bulk Upload – Payroll**")
            st.caption("Accepted: .xlsx or .csv | Required columns: emp_id, period_start, period_end")
            colPT, colPU = st.columns([1, 1])
            with colPT:
                if st.button("⬇️ Download payroll_template.xlsx"):
                    st.download_button(
                        "Save template",
                        data=download_payroll_template(),
                        file_name="payroll_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with colPU:
                pay_file = st.file_uploader("Upload Payroll file", type=["xlsx", "csv"], key="pay_upload")
                if pay_file is not None:
                    try:
                        if pay_file.name.lower().endswith(".csv"):
                            dfp = pd.read_csv(pay_file)
                        else:
                            dfp = pd.read_excel(pay_file)
                        n, msgs = import_payroll_from_df(dfp)
                        st.success(f"Imported/updated {n} payroll rows.")
                        if msgs:
                            with st.expander("Import notes"):
                                for m in msgs:
                                    st.write("- ", m)
                    except Exception as e:
                        st.error(f"Failed to import payroll: {e}")

        # ---------------- All Payroll Records Tab ----------------
        with tabs[2]:
            st.subheader("All Payroll Records")
            df_all = list_payroll_df()
            st.dataframe(df_all, use_container_width=True)
            if not df_all.empty:
                del_id = st.number_input("Delete payroll by ID", min_value=0, step=1)
                if st.button("Delete Payroll Row"):
                    if del_id > 0:
                        delete_payroll(int(del_id))
                        st.success(f"Deleted payroll id {int(del_id)}")
                    else:
                        st.warning("Enter a valid id.")

        # ---------------- Utilities Tab ----------------
        with tabs[3]:
            st.subheader("Utilities")
            if st.button("Merge duplicate payroll rows"):
                removed = merge_duplicate_payroll()
                st.success(f"Removed {removed} duplicate rows (kept latest per (emp_id, period)).")

    else:
        # ---------------- Employee Self-Service ----------------
        st.subheader("Employee Self-Service")
        emp_id = st.text_input("Enter your Employee ID")
        if emp_id:
            emp = get_employee(emp_id)
            if not emp:
                st.error("Employee ID not found.")
            else:
                df = list_payroll_df(emp_id)
                if df.empty:
                    st.info("No payroll records found.")
                else:
                    df["period"] = df["period_start"].astype(str) + " to " + df["period_end"].astype(str)
                    period = st.selectbox("Select Pay Period", options=df["period"].tolist())
                    row = df[df["period"] == period].iloc[0].to_dict()

                    gross = sum(
                        to_float(row.get(k)) for k in ["basic_pay", "overtime_pay", "allowances", "bonus"]
                    )
                    deductions = sum(
                        to_float(row.get(k))
                        for k in ["sss", "philhealth", "pagibig", "undertime", "late", "other_deductions", "tax"]
                    )
                    net = gross - deductions

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Gross Pay", peso(gross))
                    c2.metric("Deductions", peso(deductions))
                    c3.metric("Net Pay", peso(net))

                    pdf_bytes = make_payslip_pdf(row, emp)
                    fname = f"payslip_{emp_id}_{row.get('period_start')}_{row.get('period_end')}.pdf"
                    st.download_button("📥 Download PDF Payslip", data=pdf_bytes, file_name=fname, mime="application/pdf")


if __name__ == "__main__":
    main()
