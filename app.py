#!/usr/bin/env python3
"""
الكيتريك موتورز (Electric Motors) ERP v3 — Responsive Cloud-Ready Web App (Flask + SQLite)
- Designed for 1-month free trial hosting on PythonAnywhere (persistent SQLite + WSGI)
- Single responsive UI: Desktop Sidebar on PC + Bottom Navigation on Phones (PWA-ready)
- SQLite-backed session tokens (safe across WSGI workers and reloads)
- Arabic (RTL) default + English toggle
- Dashboard: Total Sales, Total Collected, Installments Receivable, Store Stock Value,
  Low Stock Alerts (<= 3), Live USD <-> SDG Exchange Rate (browser live fetch + Admin manual rate)
- RBAC (Admin, Salesman, Finance), Audit Trail, Bank-Only Payments (No Cash), Manual Installments
"""

import csv
import hashlib
import io
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "electric_motors_cloud.db")

COMPANY_NAME_AR = "الكيتريك موتورز"
COMPANY_NAME_EN = "Electric Motors (الكيتريك موتورز)"
BANKAK_ACCOUNT = "1234567 — الكيتريك موتورز"
LOW_STOCK_THRESHOLD = 3
DEFAULT_USD_SDG_RATE = 601.5

ALLOWED_BANK_METHODS = [
    "Bankak",       # بنكك - بنك الخرطوم
    "Fawry",        # فوري - بنك فيصل
    "Omdurman",     # بنك أم درمان الوطني / أووكاش
    "BankTransfer", # تحويل بنكي آخر
]

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False


def hash_password(password):
    salt = "electric_motors_cloud_v3_2026"
    return hashlib.sha256((salt + str(password)).encode("utf-8")).hexdigest()


# ==================== SQLITE DATABASE LAYER ====================
class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_schema()
        self._seed_if_empty()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self):
        with self.lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    cost_price REAL NOT NULL DEFAULT 0,
                    sell_price REAL NOT NULL DEFAULT 0,
                    stock INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_no TEXT UNIQUE NOT NULL,
                    customer_name TEXT NOT NULL,
                    customer_phone TEXT,
                    sale_type TEXT NOT NULL,
                    subtotal REAL NOT NULL,
                    markup_pct REAL NOT NULL DEFAULT 0,
                    total_amount REAL NOT NULL,
                    amount_paid REAL NOT NULL,
                    remaining_amount REAL NOT NULL,
                    payment_method TEXT NOT NULL,
                    bank_trx TEXT,
                    created_by_id INTEGER,
                    created_by_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sale_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    line_total REAL NOT NULL,
                    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS installments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER UNIQUE NOT NULL,
                    invoice_no TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    customer_phone TEXT,
                    total_contract REAL NOT NULL,
                    down_payment REAL NOT NULL,
                    financed_amount REAL NOT NULL,
                    months INTEGER NOT NULL,
                    monthly_payment REAL NOT NULL,
                    paid_amount REAL NOT NULL,
                    remaining_amount REAL NOT NULL,
                    next_due_date TEXT,
                    status TEXT NOT NULL,
                    created_by_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER,
                    invoice_no TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    payment_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    method TEXT NOT NULL,
                    bank_trx TEXT,
                    notes TEXT,
                    recorded_by_id INTEGER,
                    recorded_by_name TEXT NOT NULL,
                    paid_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _seed_if_empty(self):
        with self.lock, self._connect() as conn:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")

            if not conn.execute("SELECT 1 FROM settings WHERE key='usd_sdg_rate'").fetchone():
                conn.executemany(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    [
                        ("usd_sdg_rate", str(DEFAULT_USD_SDG_RATE), now),
                        ("usd_sdg_source", "Default / Saved", now),
                    ],
                )

            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("admin", "System Admin (المدير)", hash_password("admin123"), "admin", 1, now),
                        ("sales1", "Ahmed Sales (أحمد - مبيعات)", hash_password("1234"), "salesman", 1, now),
                        ("finance1", "Sara Finance (سارة - مالية)", hash_password("1234"), "finance", 1, now),
                    ],
                )

            if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
                seed = [
                    ("MTR-3HP", "موتور كهربائي 3 حصان — 3 Phase", "Electric Motors", 420000, 550000, 14),
                    ("MTR-5HP", "موتور كهربائي 5.5 حصان صناعي", "Electric Motors", 680000, 860000, 9),
                    ("MTR-10HP", "موتور كهربائي 10 حصان ثقيل", "Electric Motors", 1150000, 1450000, 3),
                    ("PMP-2HP", "طلمبة مياه كهربائية 2 حصان", "Water Pumps", 310000, 410000, 16),
                    ("PMP-SOL3", "طلمبة غاطسة بالطاقة الشمسية 3 حصان", "Solar Pumps", 1350000, 1700000, 2),
                    ("VFD-5KW", "انفيرتر تشغيل موتورات VFD 5.5kW", "Inverters & VFD", 590000, 760000, 8),
                    ("PNL-550", "لوح طاقة شمسية Jinko 550W", "Solar Panels", 280000, 350000, 24),
                    ("BAT-5KWL", "بطارية ليثيوم 48V 100Ah LiFePO4", "Batteries", 2600000, 3200000, 3),
                    ("CTRL-BOX", "لوحة تحكم وحماية موتور أوتوماتيك", "Accessories", 160000, 230000, 12),
                ]
                conn.executemany(
                    "INSERT INTO products (sku, name, category, cost_price, sell_price, stock, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(s[0], s[1], s[2], s[3], s[4], s[5], now) for s in seed],
                )
            conn.commit()

    def _rows(self, rows):
        return [dict(r) for r in rows]

    # --- Sessions & Auth ---
    def authenticate(self, username, password):
        uname = str(username or "").strip().lower()
        phash = hash_password(password or "")
        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, full_name, role, active FROM users WHERE LOWER(username)=? AND password_hash=?",
                (uname, phash),
            ).fetchone()
            if not row:
                return None
            if not row["active"]:
                raise ValueError("This user account is disabled.")
            token = secrets.token_hex(24)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, row["id"], now))
            conn.commit()
            return {"token": token, "user": dict(row)}

    def get_user_by_token(self, token):
        if not token:
            return None
        with self.lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.username, u.full_name, u.role, u.active
                FROM sessions s JOIN users u ON s.user_id = u.id
                WHERE s.token = ?
                """,
                (token,),
            ).fetchone()
            if not row or not row["active"]:
                return None
            return dict(row)

    def delete_session(self, token):
        if not token:
            return
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()

    # --- Exchange Rate ---
    def get_exchange_rate(self):
        with self.lock, self._connect() as conn:
            r_row = conn.execute("SELECT value, updated_at FROM settings WHERE key='usd_sdg_rate'").fetchone()
            s_row = conn.execute("SELECT value FROM settings WHERE key='usd_sdg_source'").fetchone()
            rate = float(r_row["value"]) if r_row else DEFAULT_USD_SDG_RATE
            return {
                "rate": rate,
                "source": s_row["value"] if s_row else "Default",
                "updated_at": r_row["updated_at"] if r_row else "",
            }

    def set_exchange_rate(self, rate, source="Manual (Admin)"):
        rate = float(rate)
        if rate <= 0:
            raise ValueError("Exchange rate must be greater than 0.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('usd_sdg_rate', ?, ?)", (str(round(rate, 2)), now))
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('usd_sdg_source', ?, ?)", (source, now))
            conn.commit()
        return {"rate": round(rate, 2), "source": source, "updated_at": now}

    # --- Users ---
    def list_users(self):
        with self.lock, self._connect() as conn:
            return self._rows(conn.execute("SELECT id, username, full_name, role, active, created_at FROM users ORDER BY id ASC").fetchall())

    def add_user(self, data):
        username = str(data.get("username", "")).strip().lower()
        full_name = str(data.get("full_name", "")).strip()
        password = str(data.get("password", "")).strip()
        role = str(data.get("role", "salesman")).strip().lower()
        if not username or not full_name or not password:
            raise ValueError("Username, full name, and password are required.")
        if role not in ("admin", "salesman", "finance"):
            raise ValueError("Invalid role.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, full_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                (username, full_name, hash_password(password), role, now),
            )
            conn.commit()
            return dict(conn.execute("SELECT id, username, full_name, role, active, created_at FROM users WHERE id=?", (cur.lastrowid,)).fetchone())

    def update_user(self, uid, data):
        with self.lock, self._connect() as conn:
            ex = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not ex:
                raise ValueError("User not found.")
            full_name = str(data.get("full_name", ex["full_name"])).strip()
            role = str(data.get("role", ex["role"])).strip().lower()
            active = 1 if data.get("active", ex["active"]) else 0
            new_pw = str(data.get("password", "")).strip()
            if new_pw:
                conn.execute("UPDATE users SET full_name=?, role=?, active=?, password_hash=? WHERE id=?", (full_name, role, active, hash_password(new_pw), uid))
            else:
                conn.execute("UPDATE users SET full_name=?, role=?, active=? WHERE id=?", (full_name, role, active, uid))
            conn.commit()
            return dict(conn.execute("SELECT id, username, full_name, role, active, created_at FROM users WHERE id=?", (uid,)).fetchone())

    def delete_user(self, uid, current_user_id):
        if int(uid) == int(current_user_id):
            raise ValueError("Cannot delete your own account.")
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
            return {"deleted": uid}

    # --- Products ---
    def list_products(self):
        with self.lock, self._connect() as conn:
            return self._rows(conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall())

    def add_product(self, data):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        sku = str(data.get("sku", "")).strip().upper()
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "Electric Motors")).strip()
        cost_price = float(data.get("cost_price", 0))
        sell_price = float(data.get("sell_price", 0))
        stock = int(data.get("stock", 0))
        if not sku or not name:
            raise ValueError("SKU and Product Name are required.")
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO products (sku, name, category, cost_price, sell_price, stock, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sku, name, category, cost_price, sell_price, stock, now),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone())

    def update_product(self, pid, data):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        sku = str(data.get("sku", "")).strip().upper()
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "Electric Motors")).strip()
        cost_price = float(data.get("cost_price", 0))
        sell_price = float(data.get("sell_price", 0))
        stock = int(data.get("stock", 0))
        with self.lock, self._connect() as conn:
            conn.execute(
                "UPDATE products SET sku=?, name=?, category=?, cost_price=?, sell_price=?, stock=?, updated_at=? WHERE id=?",
                (sku, name, category, cost_price, sell_price, stock, now, pid),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not row:
                raise ValueError("Product not found.")
            return dict(row)

    def restock_product(self, pid, delta):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.lock, self._connect() as conn:
            conn.execute("UPDATE products SET stock = MAX(0, stock + ?), updated_at=? WHERE id=?", (int(delta), now, pid))
            conn.commit()
            row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not row:
                raise ValueError("Product not found.")
            return dict(row)

    def delete_product(self, pid):
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
            conn.commit()
            return {"deleted": pid}

    # --- Sales ---
    def create_sale(self, data, user):
        customer_name = str(data.get("customer_name", "")).strip()
        customer_phone = str(data.get("customer_phone", "")).strip()
        sale_type = str(data.get("sale_type", "FULL")).upper()
        payment_method = str(data.get("payment_method", "Bankak"))
        bank_trx = str(data.get("bank_trx", "")).strip()
        items = data.get("items", [])

        if not customer_name:
            raise ValueError("Customer name is required.")
        if not items:
            raise ValueError("Cart is empty.")
        if sale_type not in ("FULL", "INSTALLMENT"):
            raise ValueError("Invalid sale type.")
        if payment_method not in ALLOWED_BANK_METHODS:
            raise ValueError("Only Sudanese bank payment systems are accepted (No Cash).")

        now_dt = datetime.now()
        now = now_dt.strftime("%Y-%m-%d %H:%M")
        invoice_no = f"EM-{now_dt.strftime('%Y%m%d-%H%M%S')}"

        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            resolved = []
            subtotal = 0.0
            for it in items:
                pid, qty = int(it["product_id"]), int(it["qty"])
                if qty <= 0:
                    raise ValueError("Quantity must be positive.")
                prod = cur.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                if not prod or prod["stock"] < qty:
                    raise ValueError(f"Insufficient stock for {prod['name'] if prod else pid}.")
                lt = round(prod["sell_price"] * qty, 2)
                subtotal += lt
                resolved.append({"product_id": pid, "product_name": prod["name"], "qty": qty, "unit_price": prod["sell_price"], "line_total": lt})

            if sale_type == "FULL":
                markup_pct = 0.0
                total_amount = round(subtotal, 2)
                amount_paid = total_amount
                remaining_amount = 0.0
                months, monthly_payment = 0, 0.0
            else:
                markup_pct = max(0.0, float(data.get("markup_pct", 0)))
                total_amount = round(subtotal * (1.0 + markup_pct / 100.0), 2)
                amount_paid = max(0.0, float(data.get("down_payment", 0)))
                if amount_paid >= total_amount:
                    raise ValueError("Down payment must be less than total contract amount.")
                remaining_amount = round(total_amount - amount_paid, 2)
                months = max(1, int(data.get("months", 6)))
                monthly_payment = round(remaining_amount / months, 2)

            if amount_paid > 0 and not bank_trx:
                raise ValueError("Bank Transaction Reference (Trx ID) is required.")

            cur.execute(
                """
                INSERT INTO sales (
                    invoice_no, customer_name, customer_phone, sale_type,
                    subtotal, markup_pct, total_amount, amount_paid, remaining_amount,
                    payment_method, bank_trx, created_by_id, created_by_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_no, customer_name, customer_phone, sale_type,
                    subtotal, markup_pct, total_amount, amount_paid, remaining_amount,
                    payment_method, bank_trx if amount_paid > 0 else "",
                    user["id"], user["full_name"], now,
                ),
            )
            sale_id = cur.lastrowid

            for rit in resolved:
                cur.execute(
                    "INSERT INTO sale_items (sale_id, product_id, product_name, qty, unit_price, line_total) VALUES (?, ?, ?, ?, ?, ?)",
                    (sale_id, rit["product_id"], rit["product_name"], rit["qty"], rit["unit_price"], rit["line_total"]),
                )
                cur.execute("UPDATE products SET stock = stock - ?, updated_at=? WHERE id=?", (rit["qty"], now, rit["product_id"]))

            if sale_type == "INSTALLMENT":
                next_due = (now_dt + timedelta(days=30)).strftime("%Y-%m-%d")
                cur.execute(
                    """
                    INSERT INTO installments (
                        sale_id, invoice_no, customer_name, customer_phone,
                        total_contract, down_payment, financed_amount, months,
                        monthly_payment, paid_amount, remaining_amount,
                        next_due_date, status, created_by_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (
                        sale_id, invoice_no, customer_name, customer_phone,
                        total_amount, amount_paid, remaining_amount, months,
                        monthly_payment, amount_paid, remaining_amount, next_due,
                        user["full_name"], now,
                    ),
                )

            if amount_paid > 0:
                cur.execute(
                    """
                    INSERT INTO payments (
                        sale_id, invoice_no, customer_name, payment_type,
                        amount, method, bank_trx, notes, recorded_by_id, recorded_by_name, paid_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sale_id, invoice_no, customer_name,
                        "FULL_SALE" if sale_type == "FULL" else "DOWN_PAYMENT",
                        amount_paid, payment_method, bank_trx, "POS Checkout",
                        user["id"], user["full_name"], now,
                    ),
                )

            conn.commit()
            out = dict(cur.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone())
            out["items"] = resolved
            return out

    def get_sale_detail(self, sale_id):
        with self.lock, self._connect() as conn:
            sale = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
            if not sale:
                raise ValueError("Invoice not found.")
            out = dict(sale)
            out["items"] = self._rows(conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall())
            return out

    # --- Installments & Payments ---
    def list_installments(self):
        with self.lock, self._connect() as conn:
            return self._rows(conn.execute("SELECT * FROM installments ORDER BY id DESC").fetchall())

    def pay_installment(self, inst_id, data, user):
        amount = round(float(data.get("amount", 0)), 2)
        method = str(data.get("method", "Bankak"))
        bank_trx = str(data.get("bank_trx", "")).strip()
        notes = str(data.get("notes", "Manual installment payment")).strip()

        if method not in ALLOWED_BANK_METHODS:
            raise ValueError("Only bank payment systems are accepted (No Cash).")
        if not bank_trx:
            raise ValueError("Bank Transaction Reference (Trx ID) is required.")
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0.")

        now_dt = datetime.now()
        now = now_dt.strftime("%Y-%m-%d %H:%M")

        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            inst = cur.execute("SELECT * FROM installments WHERE id=?", (inst_id,)).fetchone()
            if not inst:
                raise ValueError("Installment contract not found.")
            if inst["status"] == "PAID":
                raise ValueError("This contract is already fully paid.")
            if amount > inst["remaining_amount"] + 0.01:
                raise ValueError(f"Amount exceeds remaining balance ({inst['remaining_amount']:,.0f} SDG).")

            new_paid = round(inst["paid_amount"] + amount, 2)
            new_rem = max(0.0, round(inst["remaining_amount"] - amount, 2))
            new_status = "PAID" if new_rem <= 0.01 else "ACTIVE"
            next_due = "" if new_status == "PAID" else (now_dt + timedelta(days=30)).strftime("%Y-%m-%d")

            cur.execute(
                "UPDATE installments SET paid_amount=?, remaining_amount=?, next_due_date=?, status=? WHERE id=?",
                (new_paid, new_rem, next_due, new_status, inst_id),
            )
            cur.execute("UPDATE sales SET amount_paid=?, remaining_amount=? WHERE id=?", (new_paid, new_rem, inst["sale_id"]))
            cur.execute(
                """
                INSERT INTO payments (
                    sale_id, invoice_no, customer_name, payment_type,
                    amount, method, bank_trx, notes, recorded_by_id, recorded_by_name, paid_at
                ) VALUES (?, ?, ?, 'INSTALLMENT', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inst["sale_id"], inst["invoice_no"], inst["customer_name"],
                    amount, method, bank_trx, notes, user["id"], user["full_name"], now,
                ),
            )
            conn.commit()
            return dict(cur.execute("SELECT * FROM installments WHERE id=?", (inst_id,)).fetchone())

    def list_payments(self):
        with self.lock, self._connect() as conn:
            return self._rows(conn.execute("SELECT * FROM payments ORDER BY id DESC").fetchall())

    # --- Dashboard ---
    def get_dashboard(self):
        rate_info = self.get_exchange_rate()
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            low_stock = self._rows(
                cur.execute("SELECT id, sku, name, stock FROM products WHERE stock <= ? ORDER BY stock ASC", (LOW_STOCK_THRESHOLD,)).fetchall()
            )
            return {
                "company_name_ar": COMPANY_NAME_AR,
                "company_name_en": COMPANY_NAME_EN,
                "bankak_account": BANKAK_ACCOUNT,
                "exchange_rate": rate_info,
                "kpis": {
                    "total_sales": cur.execute("SELECT COALESCE(SUM(total_amount), 0) FROM sales").fetchone()[0],
                    "sales_count": cur.execute("SELECT COUNT(*) FROM sales").fetchone()[0],
                    "total_collected": cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments").fetchone()[0],
                    "outstanding": cur.execute("SELECT COALESCE(SUM(remaining_amount), 0) FROM installments WHERE status='ACTIVE'").fetchone()[0],
                    "active_contracts": cur.execute("SELECT COUNT(*) FROM installments WHERE status='ACTIVE'").fetchone()[0],
                    "stock_value": cur.execute("SELECT COALESCE(SUM(sell_price * stock), 0) FROM products").fetchone()[0],
                    "low_stock_count": len(low_stock),
                },
                "low_stock": low_stock,
                "recent_sales": self._rows(cur.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 25").fetchall()),
            }


db = Database(DB_PATH)


# ==================== AUTH DECORATOR ====================
def require_roles(roles=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            tok = request.headers.get("X-Auth-Token", "").strip() or request.args.get("token", "").strip()
            user = db.get_user_by_token(tok)
            if not user:
                return jsonify({"error": "Authentication required. Please log in."}), 401
            if roles and user["role"] not in roles:
                return jsonify({"error": "Access denied: Insufficient privileges."}), 403
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator


# ==================== ROUTES ====================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json", mimetype="application/manifest+json")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json(force=True) or {}
        res = db.authenticate(data.get("username"), data.get("password"))
        if not res:
            return jsonify({"error": "Invalid username or password."}), 401
        return jsonify(res)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    tok = request.headers.get("X-Auth-Token", "").strip()
    db.delete_session(tok)
    return jsonify({"status": "logged_out"})


@app.route("/api/auth/me", methods=["GET"])
@require_roles()
def api_me(user):
    return jsonify(user)


@app.route("/api/dashboard", methods=["GET"])
@require_roles(["admin", "salesman", "finance"])
def api_dashboard(user):
    return jsonify(db.get_dashboard())


@app.route("/api/rate", methods=["GET", "PUT"])
def api_rate():
    if request.method == "GET":
        tok = request.headers.get("X-Auth-Token", "").strip()
        if not db.get_user_by_token(tok):
            return jsonify({"error": "Authentication required."}), 401
        return jsonify(db.get_exchange_rate())

    tok = request.headers.get("X-Auth-Token", "").strip()
    user = db.get_user_by_token(tok)
    if not user:
        return jsonify({"error": "Authentication required."}), 401
    data = request.get_json(force=True) or {}
    source = str(data.get("source", "Manual (Admin)")).strip()
    # Live browser sync is allowed for any logged-in user; manual market override is Admin-only
    if source != "Live Online API" and user["role"] != "admin":
        return jsonify({"error": "Only Admin can set a manual exchange rate."}), 403
    try:
        return jsonify(db.set_exchange_rate(data.get("rate"), source))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/products", methods=["GET", "POST"])
def api_products():
    if request.method == "GET":
        return _list_products()
    return _add_product()


@require_roles(["admin", "salesman", "finance"])
def _list_products(user):
    return jsonify(db.list_products())


@require_roles(["admin"])
def _add_product(user):
    try:
        return jsonify(db.add_product(request.get_json(force=True) or {})), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/products/<int:pid>", methods=["PUT", "DELETE"])
@require_roles(["admin"])
def api_product_item(user, pid):
    try:
        if request.method == "PUT":
            return jsonify(db.update_product(pid, request.get_json(force=True) or {}))
        return jsonify(db.delete_product(pid))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/products/<int:pid>/restock", methods=["POST"])
@require_roles(["admin"])
def api_product_restock(user, pid):
    try:
        data = request.get_json(force=True) or {}
        return jsonify(db.restock_product(pid, data.get("delta", 1)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/sales", methods=["POST"])
@require_roles(["admin", "salesman"])
def api_create_sale(user):
    try:
        return jsonify(db.create_sale(request.get_json(force=True) or {}, user)), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/sales/<int:sid>", methods=["GET"])
@require_roles(["admin", "salesman", "finance"])
def api_sale_detail(user, sid):
    try:
        return jsonify(db.get_sale_detail(sid))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/installments", methods=["GET"])
@require_roles(["admin", "salesman", "finance"])
def api_installments(user):
    return jsonify(db.list_installments())


@app.route("/api/installments/<int:iid>/pay", methods=["POST"])
@require_roles(["admin", "salesman", "finance"])
def api_pay_installment(user, iid):
    try:
        return jsonify(db.pay_installment(iid, request.get_json(force=True) or {}, user))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/payments", methods=["GET"])
@require_roles(["admin", "salesman", "finance"])
def api_payments(user):
    return jsonify(db.list_payments())


@app.route("/api/payments/export", methods=["GET"])
@require_roles(["admin", "finance"])
def api_export_payments(user):
    rows = db.list_payments()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Invoice", "Customer", "Type", "Amount (SDG)", "Bank System", "Bank Trx ID", "Recorded By", "Notes"])
    for r in rows:
        writer.writerow([
            r["paid_at"], r["invoice_no"], r["customer_name"], r["payment_type"],
            r["amount"], r["method"], r["bank_trx"], r["recorded_by_name"], r["notes"]
        ])
    fname = f"electric_motors_payments_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.route("/api/backup", methods=["GET"])
@require_roles(["admin"])
def api_backup(user):
    with db.lock:
        with open(db.db_path, "rb") as f:
            blob = f.read()
    fname = f"electric_motors_cloud_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
    return Response(
        blob,
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.route("/api/users", methods=["GET", "POST"])
@require_roles(["admin"])
def api_users(user):
    try:
        if request.method == "GET":
            return jsonify(db.list_users())
        return jsonify(db.add_user(request.get_json(force=True) or {})), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/users/<int:uid>", methods=["PUT", "DELETE"])
@require_roles(["admin"])
def api_user_item(user, uid):
    try:
        if request.method == "PUT":
            return jsonify(db.update_user(uid, request.get_json(force=True) or {}))
        return jsonify(db.delete_user(uid, user["id"]))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port, debug=False)
