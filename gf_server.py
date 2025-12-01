import os
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# -------- إعداد مسار SQLite احتياطي (للتجريب المحلي فقط) --------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "gf_server_v2.db")

# -------- قراءة DATABASE_URL من متغيّر البيئة (Render) --------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # لو ما فيه DATABASE_URL (تشغيل محلي) نستعمل SQLite
    DATABASE_URL = f"sqlite:///{SQLITE_PATH}"

# -------- تهيئة محرك SQLAlchemy --------
engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = Flask(__name__)

# 🔐 عميل تجريبي واحد الآن (لاحقاً نعمل لوحة عملاء كاملة)
TEST_CLIENT_ID = "LOCAL-TEST"
TEST_API_KEY   = "TESTKEY123"


# ============= DB HELPERS =============

def init_db():
    """إنشاء الجداول إذا لم تكن موجودة في قاعدة PostgreSQL/SQLite."""
    with engine.begin() as conn:
        # جدول العملاء
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                name TEXT,
                api_key TEXT
            )
        """))

        # جدول الموردين
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                client_id TEXT NOT NULL,
                supplier_code TEXT,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                notes TEXT,
                UNIQUE (client_id, supplier_code)
            )
        """))

        # جدول السطور
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS lines (
                id SERIAL PRIMARY KEY,
                client_id TEXT NOT NULL,
                supplier_id INTEGER,
                reference TEXT,
                designation TEXT,
                marque TEXT,
                prix DOUBLE PRECISION,
                date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        """))

        # إدخال عميل تجريبي
        conn.execute(text("""
            INSERT INTO clients (id, name, api_key)
            VALUES (:id, :name, :key)
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": TEST_CLIENT_ID,
            "name": "Test Local Client",
            "key": TEST_API_KEY,
        })


def upsert_supplier(conn, client_id: str, supplier: dict) -> int:
    """
    حفظ/تحديث المورد في جدول السيرفر.
    conn هنا هو Connection من SQLAlchemy (engine.begin / engine.connect)
    يرجع supplier_id
    """
    code    = supplier.get("code") or supplier.get("name") or "NO-CODE"
    name    = supplier.get("name") or code
    phone   = (supplier.get("phone") or "").strip()
    email   = (supplier.get("email") or "").strip()
    address = (supplier.get("address") or "").strip()
    notes   = (supplier.get("notes") or "").strip()

    # 1) نحاول العثور على المورد أولاً
    row = conn.execute(text("""
        SELECT id, phone, email, address, notes
        FROM suppliers
        WHERE client_id = :cid AND supplier_code = :code
    """), {"cid": client_id, "code": code}).mappings().fetchone()

    if row is None:
        # 2) غير موجود → ندخله
        new_id = conn.execute(text("""
            INSERT INTO suppliers (client_id, supplier_code, name, phone, email, address, notes)
            VALUES (:cid, :code, :name, :phone, :email, :addr, :notes)
            RETURNING id
        """), {
            "cid": client_id,
            "code": code,
            "name": name,
            "phone": phone,
            "email": email,
            "addr": address,
            "notes": notes,
        }).scalar_one()
        return int(new_id)

    # 3) موجود → نحدّث فقط القيم غير الفارغة
    supplier_id = int(row["id"])
    cur_phone   = (row["phone"] or "").strip()
    cur_email   = (row["email"] or "").strip()
    cur_address = (row["address"] or "").strip()
    cur_notes   = (row["notes"] or "").strip()

    new_phone   = phone   or cur_phone
    new_email   = email   or cur_email
    new_address = address or cur_address
    new_notes   = notes   or cur_notes

    if (new_phone != cur_phone or
        new_email != cur_email or
        new_address != cur_address or
        new_notes != cur_notes):
        conn.execute(text("""
            UPDATE suppliers
            SET phone = :phone, email = :email, address = :addr, notes = :notes
            WHERE id = :id
        """), {
            "phone": new_phone,
            "email": new_email,
            "addr": new_address,
            "notes": new_notes,
            "id": supplier_id,
        })

    return supplier_id


# ============= API: استقبال السطور من GF =============

@app.post("/api/upload_lines")
def upload_lines():
    """
    GF يرسل:
    {
      "client_id": "LOCAL-TEST",
      "api_key": "TESTKEY123",
      "lines": [...]
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

    saved = 0

    # نستخدم Transaction واحدة لكل الطلب
    with engine.begin() as conn:
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

            conn.execute(text("""
                INSERT INTO lines (client_id, supplier_id, reference, designation, marque, prix, date)
                VALUES (:cid, :sid, :ref, :des, :marq, :prix, :date)
            """), {
                "cid": client_id,
                "sid": supplier_id,
                "ref": ref,
                "des": des,
                "marq": marq,
                "prix": prix,
                "date": date_val,
            })
            saved += 1

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
        <form method="get" id="searchForm">
            <input type="text"
                   id="searchInput"
                   name="q"
                   value="{{ q }}"
                   autocomplete="off"
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

<script>
(function () {
    const input = document.getElementById('searchInput');
    const form  = document.getElementById('searchForm');
    if (!input || !form) return;

    // 🟦 عند تحميل الصفحة رجّع التركيز للكومبو وحط المؤشر في آخر النص
    window.addEventListener('load', function () {
        input.focus();
        const val = input.value || "";
        try {
            input.setSelectionRange(val.length, val.length);
        } catch (e) {
            // بعض المتصفحات القديمة قد لا تدعم setSelectionRange، نتجاهل الخطأ
        }
    });

    let timer = null;

    input.addEventListener('input', function () {
        // نلغي المؤقت السابق
        if (timer) {
            clearTimeout(timer);
        }

        // ⏱ نزيد التأخير قليلاً (مثلاً 600ms) حتى لا يعيد التحميل بسرعة كبيرة
        timer = setTimeout(function () {
            form.submit();
        }, 600);
    });
})();
</script>


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


# ============= Routes الويب =============

@app.get("/")
def root():
    # تحويل بسيط مباشرة إلى العميل التجريبي
    return redirect(url_for("client_lines", client_id=TEST_CLIENT_ID))


@app.get("/client/<client_id>/lines")
def client_lines(client_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    q = (request.args.get("q") or "").strip()
    base_sql = """
        SELECT l.id, l.reference, l.designation, l.marque, l.prix, l.date,
               l.supplier_id,
               s.name as supplier_name
        FROM lines l
        LEFT JOIN suppliers s ON l.supplier_id = s.id
        WHERE l.client_id = :cid
    """
    params = {"cid": client_id}

    if q:
        base_sql += """
            AND (
                LOWER(l.reference)   LIKE :like
                OR LOWER(l.designation) LIKE :like
                OR LOWER(l.marque)   LIKE :like
                OR LOWER(s.name)     LIKE :like
            )
        """
        params["like"] = f"%{q.lower()}%"

    base_sql += " ORDER BY l.id DESC LIMIT 500"

    with engine.connect() as conn:
        result = conn.execute(text(base_sql), params)
        rows = result.mappings().all()

    return render_template_string(LINES_TEMPLATE, client_id=client_id, rows=rows, q=q)



@app.get("/client/<client_id>/supplier/<int:supplier_id>")
def supplier_page(client_id, supplier_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT * FROM suppliers
            WHERE id = :id AND client_id = :cid
        """), {"id": supplier_id, "cid": client_id}).mappings().fetchone()

    if not row:
        return "Supplier not found", 404

    return render_template_string(SUPPLIER_TEMPLATE, client_id=client_id, supplier=row)


@app.get("/client/<client_id>/line/<int:line_id>")
def line_detail(client_id, line_id):
    if client_id != TEST_CLIENT_ID:
        return "Client not found", 404

    with engine.connect() as conn:
        row = conn.execute(text("""
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
            WHERE l.id = :id AND l.client_id = :cid
        """), {"id": line_id, "cid": client_id}).mappings().fetchone()

    if not row:
        return "Line not found", 404

    return render_template_string(LINE_DETAIL_TEMPLATE, client_id=client_id, line=row)


# نستدعي init_db بمجرد استيراد الملف
init_db()

if __name__ == "__main__":
    # تشغيل محلي فقط (من دون waitress)
    app.run(host="0.0.0.0", port=8000, debug=False)

