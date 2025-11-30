import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gf_server.db")

app = Flask(__name__)

# 🔐 عميل تجريبي واحد الآن (لاحقاً نعمل لوحة عملاء كاملة)
TEST_CLIENT_ID = "LOCAL-TEST"
TEST_API_KEY   = "TESTKEY123"


# ============= DB HELPERS =============

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # جدول العملاء
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            name TEXT,
            api_key TEXT
        )
    """)

    # جدول الموردين
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            supplier_code TEXT,
            name TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            notes TEXT,
            UNIQUE (client_id, supplier_code)
        )
    """)

    # جدول السطور
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            supplier_id INTEGER,
            reference TEXT,
            designation TEXT,
            marque TEXT,
            prix REAL,
            date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    """)

    # إدخال عميل تجريبي
    cur.execute("""
        INSERT OR IGNORE INTO clients (id, name, api_key)
        VALUES (?, ?, ?)
    """, (TEST_CLIENT_ID, "Test Local Client", TEST_API_KEY))

    conn.commit()
    conn.close()


def upsert_supplier(conn, client_id: str, supplier: dict) -> int:
    """
    حفظ/تحديث المورد في جدول السيرفر.
    يرجع supplier_id
    """
    code = supplier.get("code") or supplier.get("name") or "NO-CODE"
    name = supplier.get("name") or code
    phone = supplier.get("phone") or ""
    address = supplier.get("address") or ""
    notes = supplier.get("notes") or ""

    cur = conn.cursor()
    # نحاول إدخال المورد إن لم يكن موجوداً
    cur.execute("""
        INSERT OR IGNORE INTO suppliers (client_id, supplier_code, name, phone, address, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (client_id, code, name, phone, address, notes))

    # الآن نأخذ id
    cur.execute("""
        SELECT id FROM suppliers
        WHERE client_id = ? AND supplier_code = ?
    """, (client_id, code))
    row = cur.fetchone()
    return int(row["id"]) if row else None


# ============= API: استقبال السطور من GF =============

@app.post("/api/upload_lines")
def upload_lines():
    """
    GF يرسل:
    {
      "client_id": "LOCAL-TEST",
      "api_key": "TESTKEY123",
      "lines": [
        {
          "reference": "6537E",
          "designation": "Plaquette frein",
          "marque": "Peugeot",
          "prix": 3500,
          "fournisseur": "Amin Auto",
          "date": "2025-11-30",
          "supplier": {
            "code": "SUP-001",
            "name": "Amin Auto",
            "phone": "0776 27 83 77",
            "address": "عين مليلة",
            "notes": "مورد رئيسي"
          }
        }
      ]
    }
    """
    data = request.get_json(force=True)

    client_id = data.get("client_id")
    api_key   = data.get("api_key")
    lines     = data.get("lines", [])

    # تحقق بسيط من العميل
    if client_id != TEST_CLIENT_ID or api_key != TEST_API_KEY:
        return jsonify({"ok": False, "error": "auth_failed"}), 401

    if not isinstance(lines, list) or not lines:
        return jsonify({"ok": False, "error": "no_lines"}), 400

    conn = get_conn()
    saved = 0

    try:
        for line in lines:
            ref  = (line.get("reference") or "").strip()
            des  = (line.get("designation") or "").strip()
            marq = (line.get("marque") or "").strip()
            prix = line.get("prix")
            four = (line.get("fournisseur") or "").strip()
            date_val = (line.get("date") or "").strip()

            # نحاول تحويل التاريخ للشكل YYYY-MM-DD
            if date_val:
                try:
                    # نحاول عدة صيغ
                    if " " in date_val and ":" in date_val:
                        dt = datetime.fromisoformat(date_val)
                    else:
                        dt = datetime.fromisoformat(date_val)
                    date_val = dt.date().isoformat()
                except Exception:
                    # نتركها كما هي إن فشل التحويل
                    pass

            supplier_obj = line.get("supplier") or {}
            if not supplier_obj:
                supplier_obj = {
                    "code": four or None,
                    "name": four or "المورد غير معروف"
                }

            supplier_id = upsert_supplier(conn, client_id, supplier_obj)

            cur = conn.cursor()
            cur.execute("""
                INSERT INTO lines (client_id, supplier_id, reference, designation, marque, prix, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (client_id, supplier_id, ref, des, marq, prix, date_val))
            saved += 1

        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "saved": saved})


# ============= واجهة الويب (صفحة الهاتف) =============

LINES_TEMPLATE = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8">
    <title>GF - سطور العميل {{ client_id }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {
            font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: #020617;
            color: #e5e7eb;
        }
        header {
            padding: 12px 16px;
            background: linear-gradient(135deg, #38bdf8, #0ea5e9);
            color: white;
            text-align: center;
            font-weight: 600;
            font-size: 18px;
        }
        .container {
            padding: 10px;
        }
        form.filters {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 10px;
        }
        form.filters input, form.filters select {
            padding: 6px 8px;
            border-radius: 999px;
            border: 1px solid #1e293b;
            background: #020617;
            color: #e5e7eb;
            font-size: 13px;
            outline: none;
        }
        form.filters button {
            padding: 7px 10px;
            border-radius: 999px;
            border: none;
            background: #0ea5e9;
            color: white;
            font-size: 13px;
            font-weight: 600;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: #020617;
            border-radius: 12px;
            overflow: hidden;
            font-size: 13px;
        }
        thead {
            background: #111827;
        }
        th, td {
            padding: 6px 4px;
            border-bottom: 1px solid #0b1120;
            text-align: center;
            white-space: nowrap;
        }
        tbody tr:nth-child(even) {
            background: #020617;
        }
        tbody tr:nth-child(odd) {
            background: #030712;
        }
        tbody tr:hover {
            background: #0f172a;
        }
        .small { font-size: 11px; opacity: 0.85; }
        .badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 999px;
            background: #0ea5e9;
            color: white;
            font-size: 11px;
            text-decoration: none;
        }
        .badge:hover {
            background: #0284c7;
        }
        footer {
            text-align: center;
            font-size: 11px;
            padding: 6px 0 10px;
            opacity: 0.8;
        }
    </style>
</head>
<body>
<header>
    AminosTech© GF - سطور العميل {{ client_id }}
</header>
<div class="container">

    <form class="filters" method="get">
        <input type="text" name="q" value="{{ q }}" placeholder="بحث عام (المرجع، التسمية، الماركة، المورد)">
        <button type="submit">تطبيق الفلترة</button>
    </form>

    <p class="small">
        عدد السطور المعروضة: {{ rows|length }}
    </p>

    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Reference</th>
                <th>Désignation</th>
                <th>Marque</th>
                <th>Prix</th>
                <th>Date</th>
                <th>المورد</th>
            </tr>
        </thead>
        <tbody>
            {% for r in rows %}
            <tr>
                <td class="small">{{ r["id"] }}</td>
                <td>{{ r["reference"] }}</td>
                <td>{{ r["designation"] }}</td>
                <td>{{ r["marque"] }}</td>
                <td class="small">{{ r["prix"] }}</td>
                <td class="small">{{ r["date"] }}</td>
                <td>
                    {% if r["supplier_id"] %}
                        <a class="badge" href="{{ url_for('supplier_page', client_id=client_id, supplier_id=r['supplier_id']) }}">بيانات المورد</a>
                    {% else %}
                        <span class="small">غير محدد</span>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <footer>
        AminosTech© Gestion Fournisseur • نسخة موبايل (قراءة فقط)
    </footer>

</div>
</body>
</html>
"""


SUPPLIER_TEMPLATE = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8">
    <title>بيانات المورد</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {
            font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: #020617;
            color: #e5e7eb;
        }
        .card {
            margin: 16px;
            padding: 14px;
            border-radius: 18px;
            background: #020617;
            box-shadow: 0 10px 30px rgba(15,23,42,0.7);
            border: 1px solid #1f2937;
        }
        h1 {
            margin: 0 0 10px 0;
            font-size: 18px;
            color: #f9fafb;
        }
        .label {
            font-size: 11px;
            opacity: 0.7;
        }
        .value {
            font-size: 14px;
            margin-bottom: 8px;
        }
        .pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            background: #0ea5e9;
            color: white;
            font-size: 11px;
        }
        a.back {
            display: inline-block;
            margin-top: 10px;
            font-size: 12px;
            color: #38bdf8;
            text-decoration: none;
        }
    </style>
</head>
<body>
<div class="card">
    <h1>{{ supplier["name"] }}</h1>

    <div class="label">الكود</div>
    <div class="value"><span class="pill">{{ supplier["supplier_code"] or "غير محدد" }}</span></div>

    <div class="label">الهاتف</div>
    <div class="value">{{ supplier["phone"] or "غير مسجل" }}</div>

    <div class="label">العنوان</div>
    <div class="value">{{ supplier["address"] or "غير مسجل" }}</div>

    <div class="label">ملاحظات</div>
    <div class="value">{{ supplier["notes"] or "لا توجد ملاحظات" }}</div>

    <a class="back" href="{{ url_for('client_lines', client_id=client_id) }}">
        ⬅ الرجوع إلى السطور
    </a>
</div>
</body>
</html>
"""


@app.get("/")
def root():
    # تحويل بسيط مباشرة إلى العميل التجريبي
    return redirect(url_for("client_lines", client_id=TEST_CLIENT_ID))


@app.get("/client/<client_id>/lines")
def client_lines(client_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    q = (request.args.get("q") or "").strip()

    conn = get_conn()
    cur = conn.cursor()

    base_sql = """
        SELECT l.id, l.reference, l.designation, l.marque, l.prix, l.date,
               l.supplier_id,
               s.name as supplier_name
        FROM lines l
        LEFT JOIN suppliers s ON l.supplier_id = s.id
        WHERE l.client_id = ?
    """
    params = [client_id]

    if q:
        base_sql += """
            AND (
                l.reference   LIKE ?
                OR l.designation LIKE ?
                OR l.marque   LIKE ?
                OR s.name     LIKE ?
            )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like])

    base_sql += " ORDER BY l.id DESC LIMIT 500"

    cur.execute(base_sql, params)
    rows = cur.fetchall()
    conn.close()

    return render_template_string(LINES_TEMPLATE, client_id=client_id, rows=rows, q=q)


@app.get("/client/<client_id>/supplier/<int:supplier_id>")
def supplier_page(client_id, supplier_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM suppliers
        WHERE id = ? AND client_id = ?
    """, (supplier_id, client_id))
    supplier = cur.fetchone()
    conn.close()

    if not supplier:
        return "Supplier not found", 404

    return render_template_string(SUPPLIER_TEMPLATE, client_id=client_id, supplier=supplier)


if __name__ == "__main__":
    init_db()
    # للسيرفر المحلي: 0.0.0.0 يعني يشتغل على كل العناوين في الشبكة
    app.run(host="0.0.0.0", port=8000, debug=False)
