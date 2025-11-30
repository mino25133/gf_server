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
        # جدول الموردين
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            supplier_code TEXT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
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
    code    = supplier.get("code") or supplier.get("name") or "NO-CODE"
    name    = supplier.get("name") or code
    phone   = supplier.get("phone") or ""
    email   = supplier.get("email") or ""
    address = supplier.get("address") or ""
    notes   = supplier.get("notes") or ""

    cur = conn.cursor()
    # نحاول إدخال المورد إن لم يكن موجوداً
    cur.execute("""
        INSERT OR IGNORE INTO suppliers (client_id, supplier_code, name, phone, email, address, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (client_id, code, name, phone, email, address, notes))

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
<html lang="fr">
<head>
    <meta charset="utf-8">
    <title>GF - Lignes du client {{ client_id }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        :root {
            --bg: #020617;
            --bg-card: #020617;
            --bg-card-soft: #020617;
            --border: #1f2937;
            --accent: #0ea5e9;
            --accent-soft: #38bdf8;
            --text-main: #e5e7eb;
            --text-muted: #9ca3af;
        }

        * {
            box-sizing: border-box;
        }

        body {
            font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: var(--bg);
            color: var(--text-main);
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
            max-width: 520px;
            margin: 0 auto;
            padding: 10px 10px 16px 10px;
        }

        .filters {
            margin-bottom: 10px;
        }

        .filters form {
            display: flex;
            flex-direction: row;
            gap: 6px;
        }

        .filters input[type="text"] {
            flex: 1;
            padding: 9px 11px;
            border-radius: 999px;
            border: 1px solid #1e293b;
            background: #020617;
            color: var(--text-main);
            font-size: 14px;
            outline: none;
        }

        .filters input[type="text"]::placeholder {
            color: var(--text-muted);
        }

        .filters button {
            border: none;
            border-radius: 999px;
            padding: 8px 12px;
            background: var(--accent);
            color: #fff;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }

        .summary {
            font-size: 12px;
            color: var(--text-muted);
            margin: 4px 2px 10px 2px;
        }

        .card {
            display: block;
            background: var(--bg-card);
            border-radius: 16px;
            border: 1px solid var(--border);
            box-shadow: 0 10px 30px rgba(15,23,42,0.6);
            padding: 10px 11px;
            margin-bottom: 8px;
            text-decoration: none;
            color: inherit;
        }

        .card-top {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 4px;
        }

        .ref {
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 0.04em;
        }

        .prix {
            font-size: 15px;
            font-weight: 600;
            color: var(--accent-soft);
        }

        .designation {
            font-size: 14px;
            margin-bottom: 6px;
        }

        .meta-row {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            align-items: center;
            margin-bottom: 4px;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 11px;
            border: 1px solid #1f2937;
            background: #020617;
            color: var(--text-main);
            text-decoration: none;
        }

        .badge-marque {
            border-color: #111827;
        }

        .badge-fournisseur {
            border-color: var(--accent);
        }

        .badge-fournisseur span.icon {
            font-size: 13px;
            margin-right: 4px;
        }

        .date {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        .no-data {
            text-align: center;
            color: var(--text-muted);
            font-size: 14px;
            margin-top: 30px;
        }

        footer {
            text-align: center;
            font-size: 11px;
            padding-top: 10px;
            color: var(--text-muted);
        }
    </style>
</head>
<body>
<header>
    AminosTech© GF — Lignes du client {{ client_id }}
</header>

<div class="container">

    <div class="filters">
        <form method="get">
            <input type="text"
                   name="q"
                   value="{{ q }}"
                   placeholder="Recherche : référence, désignation, marque ou fournisseur">
            <button type="submit">OK</button>
        </form>
    </div>

    <div class="summary">
        {{ rows|length }} lignes trouvées
        {% if q %}
            • filtre : « {{ q }} »
        {% endif %}
    </div>

    {% if rows|length == 0 %}
        <div class="no-data">
            Aucune ligne à afficher pour le moment.
        </div>
    {% else %}
        {% for r in rows %}
            <a class="card"
   href="{{ url_for('line_detail', client_id=client_id, line_id=r['id']) }}">

                <div class="card-top">
                    <div class="ref">{{ r["reference"] or "—" }}</div>
                    <div class="prix">
                        {% if r["prix"] is not none %}
                            {{ r["prix"] }}
                        {% else %}
                            —
                        {% endif %}
                    </div>
                </div>

                <div class="designation">
                    {{ r["designation"] or "" }}
                </div>

                <div class="meta-row">
                    <div class="badge badge-marque">
                        {{ r["marque"] or "Sans marque" }}
                    </div>

                    <div class="badge badge-fournisseur">
                        <span class="icon">👤</span>
                        {{ r["supplier_name"] or "Fournisseur inconnu" }}
                    </div>
                </div>

                <div class="date">
                    Date : {{ r["date"] or "—" }} • ID: {{ r["id"] }}
                </div>
            </a>
        {% endfor %}
    {% endif %}

    <footer>
        AminosTech© Gestion Fournisseur — Vue mobile (lecture seule)
    </footer>
</div>
</body>
</html>
"""

LINE_DETAIL_TEMPLATE = """
<!doctype html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <title>Détail de la ligne</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        :root {
            --bg: #020617;
            --bg-card: #020617;
            --border: #1f2937;
            --accent: #0ea5e9;
            --text-main: #e5e7eb;
            --text-muted: #9ca3af;
        }

        * {
            box-sizing: border-box;
        }

        body {
            font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: var(--bg);
            color: var(--text-main);
        }

        .card {
            max-width: 520px;
            margin: 18px auto;
            padding: 14px 14px 18px 14px;
            border-radius: 18px;
            background: var(--bg-card);
            box-shadow: 0 12px 32px rgba(15,23,42,0.7);
            border: 1px solid var(--border);
        }

        h1 {
            margin: 0 0 10px 0;
            font-size: 20px;
            color: #f9fafb;
        }

        .line {
            margin-bottom: 9px;
        }

        .label {
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 2px;
        }

        .value {
            font-size: 14px;
        }

        .pill-ref {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            background: #0ea5e9;
            color: #fff;
            font-weight: 600;
            font-size: 13px;
            letter-spacing: 0.05em;
        }

        .pill-marque {
            display: inline-block;
            padding: 3px 9px;
            border-radius: 999px;
            border: 1px solid #1f2937;
            font-size: 12px;
        }

        .pill-prix {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            background: #22c55e22;
            border: 1px solid #22c55e55;
            font-size: 13px;
        }

        .value-strong {
            font-size: 15px;
            font-weight: 500;
        }

        .back {
            display: inline-block;
            margin-top: 14px;
            font-size: 13px;
            color: var(--accent);
            text-decoration: none;
        }

        .supplier-name {
            font-weight: 600;
            font-size: 14px;
        }
    </style>
</head>
<body>
<div class="card">
    <h1>Détail de la ligne</h1>

    <div class="line">
        <div class="label">Référence</div>
        <div class="value">
            <span class="pill-ref">{{ line["reference"] or "—" }}</span>
        </div>
    </div>

    <div class="line">
        <div class="label">Désignation</div>
        <div class="value value-strong">{{ line["designation"] or "—" }}</div>
    </div>

    <div class="line">
        <div class="label">Marque</div>
        <div class="value">
            <span class="pill-marque">{{ line["marque"] or "Sans marque" }}</span>
        </div>
    </div>

    <div class="line">
        <div class="label">Prix</div>
        <div class="value">
            {% if line["prix"] is not none %}
                <span class="pill-prix">{{ line["prix"] }}</span>
            {% else %}
                —
            {% endif %}
        </div>
    </div>

    <div class="line">
        <div class="label">Date</div>
        <div class="value">{{ line["date"] or "—" }}</div>
    </div>

    <div class="line">
        <div class="label">Fournisseur</div>
        <div class="value">
            <div class="supplier-name">
                {{ line["supplier_name"] or "Fournisseur inconnu" }}
            </div>
        </div>
    </div>

    <div class="line">
        <div class="label">Téléphone du fournisseur</div>
        <div class="value">{{ line["supplier_phone"] or "Non renseigné" }}</div>
    </div>

    <div class="line">
        <div class="label">E-mail du fournisseur</div>
        <div class="value">{{ line["supplier_email"] or "Non renseigné" }}</div>
    </div>

    <a class="back" href="{{ url_for('client_lines', client_id=client_id) }}">
        ⬅ Retour à la liste
    </a>
</div>
</body>
</html>
"""


SUPPLIER_TEMPLATE = """
<!doctype html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <title>Fiche fournisseur</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        :root {
            --bg: #020617;
            --bg-card: #020617;
            --border: #1f2937;
            --accent: #0ea5e9;
            --text-main: #e5e7eb;
            --text-muted: #9ca3af;
        }

        * {
            box-sizing: border-box;
        }

        body {
            font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
            margin: 0;
            padding: 0;
            background: var(--bg);
            color: var(--text-main);
        }

        .card {
            max-width: 520px;
            margin: 18px auto;
            padding: 14px 14px 16px 14px;
            border-radius: 18px;
            background: var(--bg-card);
            box-shadow: 0 12px 32px rgba(15,23,42,0.7);
            border: 1px solid var(--border);
        }

        h1 {
            margin: 0 0 8px 0;
            font-size: 20px;
            color: #f9fafb;
        }

        .line {
            margin-bottom: 8px;
        }

        .label {
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 2px;
        }

        .value {
            font-size: 14px;
        }

        .pill {
            display: inline-block;
            padding: 3px 9px;
            border-radius: 999px;
            background: var(--accent);
            color: #fff;
            font-size: 11px;
        }

        a.back {
            display: inline-block;
            margin-top: 12px;
            font-size: 13px;
            color: var(--accent);
            text-decoration: none;
        }
    </style>
</head>
<body>
<div class="card">
    <h1>{{ supplier["name"] }}</h1>

    <div class="line">
        <div class="label">Code fournisseur</div>
        <div class="value">
            <span class="pill">{{ supplier["supplier_code"] or "Non défini" }}</span>
        </div>
    </div>

    <div class="line">
        <div class="label">Téléphone</div>
        <div class="value">{{ supplier["phone"] or "Non renseigné" }}</div>
    </div>

    <div class="line">
        <div class="label">Adresse</div>
        <div class="value">{{ supplier["address"] or "Non renseignée" }}</div>
    </div>

    <div class="line">
        <div class="label">Notes</div>
        <div class="value">{{ supplier["notes"] or "Aucune note" }}</div>
    </div>

    <a class="back" href="{{ url_for('client_lines', client_id=client_id) }}">
        ⬅ Retour aux lignes
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
@app.get("/client/<client_id>/line/<int:line_id>")
def line_detail(client_id, line_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            l.id,
            l.reference,
            l.designation,
            l.marque,
            l.prix,
            l.date,
            s.name  AS supplier_name,
            s.phone AS supplier_phone,
            s.email AS supplier_email
        FROM lines l
        LEFT JOIN suppliers s ON l.supplier_id = s.id
        WHERE l.id = ? AND l.client_id = ?
    """, (line_id, client_id))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "Line not found", 404

    return render_template_string(LINE_DETAIL_TEMPLATE, client_id=client_id, line=row)
