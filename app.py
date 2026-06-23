"""
Multi-Perfil IA — backend Flask
================================
Un solo servicio Flask que:
  1) Sirve el frontend (Mini App de Telegram) en "/"
  2) Expone la API REST en "/api/*"
  3) Guarda todo en SQLite, separado por tg_id (el ID de Telegram de cada usuario)

Mismo patrón que el VPS Simulator: un Flask + SQLite en Railway, sin
dependencias pesadas. No valida el "initData" firmado de Telegram (igual que
el VPS Simulator tampoco lo hacía) — para uso personal es suficiente, pero si
algún día abrís esto a más gente conviene agregar esa verificación HMAC.
"""

import os
import sqlite3
import time
import uuid
from contextlib import closing

from flask import Flask, g, jsonify, request, send_from_directory

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------------------
# Conexión a la base
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                tg_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                tg_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                service TEXT NOT NULL,
                email TEXT,
                plan TEXT,
                limit_count INTEGER,
                limit_reset_hours REAL,
                last_used TEXT,
                notes TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_profiles_tg ON profiles(tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_tg ON accounts(tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_profile ON accounts(profile_id);
            """
        )
        db.commit()


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Serialización (nombres de campo en camelCase, igual que el frontend)
# ---------------------------------------------------------------------------
def serialize_profile(row):
    return {"id": row["id"], "name": row["name"], "createdAt": row["created_at"]}


def serialize_account(row):
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "service": row["service"],
        "email": row["email"] or "",
        "plan": row["plan"] or "Free",
        "limitCount": row["limit_count"],
        "limitResetHours": row["limit_reset_hours"],
        "lastUsed": row["last_used"] or "",
        "notes": row["notes"] or "",
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
    }


def require_tg_id(source):
    tg_id = source.get("tg_id")
    if not tg_id:
        return None
    return str(tg_id)


def error(msg, code=400):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Estado completo (carga inicial + refresco en segundo plano)
# ---------------------------------------------------------------------------
@app.route("/api/state")
def get_state():
    tg_id = require_tg_id(request.args)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    profiles = db.execute(
        "SELECT * FROM profiles WHERE tg_id=? ORDER BY created_at ASC", (tg_id,)
    ).fetchall()
    accounts = db.execute(
        "SELECT * FROM accounts WHERE tg_id=? ORDER BY created_at ASC", (tg_id,)
    ).fetchall()
    return jsonify(
        {
            "profiles": [serialize_profile(p) for p in profiles],
            "accounts": [serialize_account(a) for a in accounts],
        }
    )


# ---------------------------------------------------------------------------
# Perfiles
# ---------------------------------------------------------------------------
@app.route("/api/profiles", methods=["POST"])
def create_profile():
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    name = (body.get("name") or "").strip()
    if not tg_id or not name:
        return error("faltan tg_id o name")
    db = get_db()
    row = {"id": new_id("p"), "tg_id": tg_id, "name": name, "created_at": now_ms()}
    db.execute(
        "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
        (row["id"], row["tg_id"], row["name"], row["created_at"]),
    )
    db.commit()
    return jsonify({"profile": {"id": row["id"], "name": name, "createdAt": row["created_at"]}})


@app.route("/api/profiles/bulk", methods=["POST"])
def create_profiles_bulk():
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    if not tg_id:
        return error("falta tg_id")
    prefix = (body.get("prefix") or "Perfil").strip() or "Perfil"
    try:
        start = int(body.get("from"))
        end = int(body.get("to"))
    except (TypeError, ValueError):
        return error("from/to inválidos")
    if start > end or end - start > 200:
        return error("rango inválido (máx. 200 a la vez)")

    db = get_db()
    existing = {
        r["name"] for r in db.execute("SELECT name FROM profiles WHERE tg_id=?", (tg_id,))
    }
    created = []
    for i in range(start, end + 1):
        name = f"{prefix} {i}"
        if name in existing:
            continue
        row = {"id": new_id("p"), "name": name, "created_at": now_ms()}
        db.execute(
            "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
            (row["id"], tg_id, name, row["created_at"]),
        )
        created.append({"id": row["id"], "name": name, "createdAt": row["created_at"]})
    db.commit()
    return jsonify({"profiles": created})


@app.route("/api/profiles/<profile_id>", methods=["PATCH"])
def update_profile(profile_id):
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    name = (body.get("name") or "").strip()
    if not tg_id or not name:
        return error("faltan tg_id o name")
    db = get_db()
    cur = db.execute(
        "UPDATE profiles SET name=? WHERE id=? AND tg_id=?", (name, profile_id, tg_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return error("perfil no encontrado", 404)
    return jsonify({"ok": True})


@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    tg_id = require_tg_id(request.args)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    cur = db.execute("DELETE FROM profiles WHERE id=? AND tg_id=?", (profile_id, tg_id))
    db.execute("DELETE FROM accounts WHERE profile_id=? AND tg_id=?", (profile_id, tg_id))
    db.commit()
    if cur.rowcount == 0:
        return error("perfil no encontrado", 404)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Cuentas IA
# ---------------------------------------------------------------------------
ACCOUNT_FIELDS = ["service", "email", "plan", "limitCount", "limitResetHours", "lastUsed", "notes"]
ACCOUNT_COLUMNS = {
    "service": "service",
    "email": "email",
    "plan": "plan",
    "limitCount": "limit_count",
    "limitResetHours": "limit_reset_hours",
    "lastUsed": "last_used",
    "notes": "notes",
}


@app.route("/api/accounts", methods=["POST"])
def create_account():
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    profile_id = body.get("profileId")
    service = (body.get("service") or "").strip()
    if not tg_id or not profile_id or not service:
        return error("faltan tg_id, profileId o service")

    db = get_db()
    owns = db.execute(
        "SELECT 1 FROM profiles WHERE id=? AND tg_id=?", (profile_id, tg_id)
    ).fetchone()
    if not owns:
        return error("perfil no encontrado", 404)

    row_id = new_id("a")
    created_at = now_ms()
    db.execute(
        """INSERT INTO accounts
           (id, tg_id, profile_id, service, email, plan, limit_count, limit_reset_hours,
            last_used, notes, enabled, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            row_id, tg_id, profile_id, service,
            (body.get("email") or "").strip(),
            (body.get("plan") or "Free").strip() or "Free",
            body.get("limitCount"),
            body.get("limitResetHours"),
            body.get("lastUsed") or "",
            body.get("notes") or "",
            created_at,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})


@app.route("/api/accounts/<account_id>", methods=["PATCH"])
def update_account(account_id):
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    existing = db.execute(
        "SELECT * FROM accounts WHERE id=? AND tg_id=?", (account_id, tg_id)
    ).fetchone()
    if not existing:
        return error("cuenta no encontrada", 404)

    sets, params = [], []
    for field in ACCOUNT_FIELDS:
        if field in body:
            sets.append(f"{ACCOUNT_COLUMNS[field]}=?")
            params.append(body[field])
    if "profileId" in body:
        sets.append("profile_id=?")
        params.append(body["profileId"])
    if not sets:
        return error("nada para actualizar")
    params.extend([account_id, tg_id])
    db.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=? AND tg_id=?", params)
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def delete_account(account_id):
    tg_id = require_tg_id(request.args)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    cur = db.execute("DELETE FROM accounts WHERE id=? AND tg_id=?", (account_id, tg_id))
    db.commit()
    if cur.rowcount == 0:
        return error("cuenta no encontrada", 404)
    return jsonify({"ok": True})


@app.route("/api/accounts/<account_id>/mark-used", methods=["POST"])
def mark_used(account_id):
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    from datetime import datetime, timezone

    iso_now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "UPDATE accounts SET last_used=? WHERE id=? AND tg_id=?", (iso_now, account_id, tg_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return error("cuenta no encontrada", 404)
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})


@app.route("/api/accounts/<account_id>/toggle-enabled", methods=["POST"])
def toggle_enabled(account_id):
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    row = db.execute(
        "SELECT * FROM accounts WHERE id=? AND tg_id=?", (account_id, tg_id)
    ).fetchone()
    if not row:
        return error("cuenta no encontrada", 404)
    new_val = 0 if row["enabled"] else 1
    db.execute("UPDATE accounts SET enabled=? WHERE id=?", (new_val, account_id))
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})


# ---------------------------------------------------------------------------
# Export / Import / Reset
# ---------------------------------------------------------------------------
@app.route("/api/export")
def export_data():
    tg_id = require_tg_id(request.args)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    profiles = db.execute("SELECT * FROM profiles WHERE tg_id=?", (tg_id,)).fetchall()
    accounts = db.execute("SELECT * FROM accounts WHERE tg_id=?", (tg_id,)).fetchall()
    return jsonify(
        {
            "profiles": [serialize_profile(p) for p in profiles],
            "accounts": [serialize_account(a) for a in accounts],
        }
    )


@app.route("/api/import", methods=["POST"])
def import_data():
    body = request.get_json(force=True) or {}
    tg_id = require_tg_id(body)
    if not tg_id:
        return error("falta tg_id")
    profiles_in = body.get("profiles") or []
    accounts_in = body.get("accounts") or []
    mode = body.get("mode", "merge")  # 'merge' o 'replace'

    db = get_db()
    if mode == "replace":
        db.execute("DELETE FROM accounts WHERE tg_id=?", (tg_id,))
        db.execute("DELETE FROM profiles WHERE tg_id=?", (tg_id,))

    id_map = {}
    for p in profiles_in:
        new_pid = new_id("p")
        id_map[p.get("id")] = new_pid
        db.execute(
            "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
            (new_pid, tg_id, p.get("name") or "Sin nombre", p.get("createdAt") or now_ms()),
        )
    for a in accounts_in:
        mapped_profile_id = id_map.get(a.get("profileId"), a.get("profileId"))
        db.execute(
            """INSERT INTO accounts
               (id, tg_id, profile_id, service, email, plan, limit_count, limit_reset_hours,
                last_used, notes, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id("a"), tg_id, mapped_profile_id,
                a.get("service") or "IA", a.get("email") or "", a.get("plan") or "Free",
                a.get("limitCount"), a.get("limitResetHours"), a.get("lastUsed") or "",
                a.get("notes") or "", 1 if a.get("enabled", True) else 0,
                a.get("createdAt") or now_ms(),
            ),
        )
    db.commit()
    return jsonify({"ok": True, "profiles": len(profiles_in), "accounts": len(accounts_in)})


@app.route("/api/reset", methods=["DELETE"])
def reset_all():
    tg_id = require_tg_id(request.args)
    if not tg_id:
        return error("falta tg_id")
    db = get_db()
    db.execute("DELETE FROM accounts WHERE tg_id=?", (tg_id,))
    db.execute("DELETE FROM profiles WHERE tg_id=?", (tg_id,))
    db.commit()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
