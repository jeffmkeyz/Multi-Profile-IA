"""
Multi-Perfil IA — backend Flask (Versión Segura)
================================================
Incluye:
1. Validación criptográfica de Telegram (initData).
2. Encriptación AES (Fernet) para API Keys.
3. Rate Limiting para evitar abusos y ataques DDoS.
"""

import os
import sqlite3
import time
import uuid
import hashlib
import hmac
import json
import requests
from urllib.parse import parse_qsl
from contextlib import closing

from flask import Flask, g, jsonify, request, send_from_directory, abort
from cryptography.fernet import Fernet
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- Configuración de Encriptación ---
# Debe ser una key generada por Fernet.generate_key(). Si no existe en .env, genera una temporal.
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    print("⚠️ ADVERTENCIA: No se encontró ENCRYPTION_KEY. Usando una temporal (las API Keys se perderán al reiniciar).")
    ENCRYPTION_KEY = Fernet.generate_key()
fernet = Fernet(ENCRYPTION_KEY)

app = Flask(__name__, static_folder=None)

# --- Rate Limiting ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["3000 per day", "500 per hour"],
    storage_uri="memory://"
)

def encrypt_data(data: str) -> str:
    if not data: return ""
    return fernet.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    if not data: return ""
    try:
        return fernet.decrypt(data.encode()).decode()
    except:
        return ""

# ---------------------------------------------------------------------------
# Seguridad: Validación de Telegram
# ---------------------------------------------------------------------------
def validate_tg_data(init_data: str) -> str:
    """Valida la firma criptográfica de Telegram y extrae el ID real."""
    if not init_data or not BOT_TOKEN:
        return None
    
    parsed_data = dict(parse_qsl(init_data))
    if "hash" not in parsed_data:
        return None
        
    hash_val = parsed_data.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if calc_hash == hash_val:
        user_data = json.loads(parsed_data.get("user", "{}"))
        return str(user_data.get("id"))
    return None

@app.before_request
def protect_api():
    """Middleware: Protege todas las rutas /api/ (excepto healthz)"""
    if request.path.startswith("/api/") and request.path != "/api/healthz":
        # Extraemos la firma de los headers enviados por la Mini App
        init_data = request.headers.get("Authorization")
        
        # Modo de desarrollo local (opcional, quitar en producción estricta)
        if init_data and init_data.startswith("DEV_MODE_"):
            g.real_tg_id = init_data.replace("DEV_MODE_", "")
            return

        if not init_data:
            abort(401, description="No autorizado. Falta firma de Telegram.")
            
        real_tg_id = validate_tg_data(init_data)
        if not real_tg_id:
            abort(403, description="Firma inválida o alterada.")
            
        # Almacenamos el ID de forma segura para usarlo en los endpoints
        g.real_tg_id = real_tg_id

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
                api_key TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_profiles_tg ON profiles(tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_tg ON accounts(tg_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_profile ON accounts(profile_id);
            """
        )
        # MIGRACIÓN SEGURA: Añadir api_key si no existe en BD antigua
        try:
            db.execute("ALTER TABLE accounts ADD COLUMN api_key TEXT")
        except sqlite3.OperationalError:
            pass # La columna ya existe
        db.commit()

def new_id(prefix): return f"{prefix}_{uuid.uuid4().hex[:12]}"
def now_ms(): return int(time.time() * 1000)

# ---------------------------------------------------------------------------
# Serialización
# ---------------------------------------------------------------------------
def serialize_profile(row):
    return {"id": row["id"], "name": row["name"], "createdAt": row["created_at"]}

def serialize_account(row):
    # Intentar obtener api_key (por compatibilidad con bases viejas)
    api_key_enc = ""
    if "api_key" in row.keys():
        api_key_enc = row["api_key"]
        
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
        "apiKey": decrypt_data(api_key_enc), # Desencriptar para enviarla al frontend
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
    }

def error(msg, code=400): return jsonify({"error": msg}), code

# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------
@app.route("/")
def index(): return send_from_directory(STATIC_DIR, "index.html")

@app.route("/healthz")
@limiter.exempt
def healthz(): return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# Endpoints (Usan g.real_tg_id de forma segura)
# ---------------------------------------------------------------------------
@app.route("/api/state")
def get_state():
    db = get_db()
    profiles = db.execute("SELECT * FROM profiles WHERE tg_id=? ORDER BY created_at ASC", (g.real_tg_id,)).fetchall()
    accounts = db.execute("SELECT * FROM accounts WHERE tg_id=? ORDER BY created_at ASC", (g.real_tg_id,)).fetchall()
    return jsonify({
        "profiles": [serialize_profile(p) for p in profiles],
        "accounts": [serialize_account(a) for a in accounts],
    })

@app.route("/api/profiles", methods=["POST"])
@limiter.limit("20 per minute")
def create_profile():
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name: return error("falta name")
    
    db = get_db()
    row = {"id": new_id("p"), "tg_id": g.real_tg_id, "name": name, "created_at": now_ms()}
    db.execute(
        "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
        (row["id"], row["tg_id"], row["name"], row["created_at"])
    )
    db.commit()
    return jsonify({"profile": {"id": row["id"], "name": name, "createdAt": row["created_at"]}})

@app.route("/api/profiles/bulk", methods=["POST"])
@limiter.limit("5 per minute")
def create_profiles_bulk():
    body = request.get_json(force=True) or {}
    prefix = (body.get("prefix") or "Perfil").strip() or "Perfil"
    try:
        start, end = int(body.get("from")), int(body.get("to"))
    except:
        return error("from/to inválidos")
    if start > end or end - start > 200:
        return error("rango inválido (máx. 200 a la vez)")

    db = get_db()
    existing = {r["name"] for r in db.execute("SELECT name FROM profiles WHERE tg_id=?", (g.real_tg_id,))}
    created = []
    for i in range(start, end + 1):
        name = f"{prefix} {i}"
        if name in existing: continue
        row = {"id": new_id("p"), "name": name, "created_at": now_ms()}
        db.execute(
            "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
            (row["id"], g.real_tg_id, name, row["created_at"])
        )
        created.append({"id": row["id"], "name": name, "createdAt": row["created_at"]})
    db.commit()
    return jsonify({"profiles": created})

@app.route("/api/profiles/<profile_id>", methods=["PATCH"])
def update_profile(profile_id):
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name: return error("falta name")
    db = get_db()
    cur = db.execute("UPDATE profiles SET name=? WHERE id=? AND tg_id=?", (name, profile_id, g.real_tg_id))
    db.commit()
    if cur.rowcount == 0: return error("perfil no encontrado", 404)
    return jsonify({"ok": True})

@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    db = get_db()
    cur = db.execute("DELETE FROM profiles WHERE id=? AND tg_id=?", (profile_id, g.real_tg_id))
    db.execute("DELETE FROM accounts WHERE profile_id=? AND tg_id=?", (profile_id, g.real_tg_id))
    db.commit()
    if cur.rowcount == 0: return error("perfil no encontrado", 404)
    return jsonify({"ok": True})

# -- Cuentas --
ACCOUNT_FIELDS = ["service", "email", "plan", "limitCount", "limitResetHours", "lastUsed", "notes"]
ACCOUNT_COLUMNS = {"service": "service", "email": "email", "plan": "plan", "limitCount": "limit_count", "limitResetHours": "limit_reset_hours", "lastUsed": "last_used", "notes": "notes"}

@app.route("/api/accounts", methods=["POST"])
@limiter.limit("50 per minute")
def create_account():
    body = request.get_json(force=True) or {}
    profile_id = body.get("profileId")
    service = (body.get("service") or "").strip()
    if not profile_id or not service: return error("faltan profileId o service")

    db = get_db()
    if not db.execute("SELECT 1 FROM profiles WHERE id=? AND tg_id=?", (profile_id, g.real_tg_id)).fetchone():
        return error("perfil no encontrado", 404)

    row_id, created_at = new_id("a"), now_ms()
    enc_api_key = encrypt_data((body.get("apiKey") or "").strip())

    db.execute(
        """INSERT INTO accounts
           (id, tg_id, profile_id, service, email, plan, limit_count, limit_reset_hours, last_used, notes, api_key, enabled, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            row_id, g.real_tg_id, profile_id, service,
            (body.get("email") or "").strip(), (body.get("plan") or "Free").strip() or "Free",
            body.get("limitCount"), body.get("limitResetHours"), body.get("lastUsed") or "",
            body.get("notes") or "", enc_api_key, created_at
        )
    )
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (row_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})

@app.route("/api/accounts/<account_id>", methods=["PATCH"])
def update_account(account_id):
    body = request.get_json(force=True) or {}
    db = get_db()
    if not db.execute("SELECT * FROM accounts WHERE id=? AND tg_id=?", (account_id, g.real_tg_id)).fetchone():
        return error("cuenta no encontrada", 404)

    sets, params = [], []
    for field in ACCOUNT_FIELDS:
        if field in body:
            sets.append(f"{ACCOUNT_COLUMNS[field]}=?")
            params.append(body[field])
    if "profileId" in body:
        sets.append("profile_id=?")
        params.append(body["profileId"])
    if "apiKey" in body:
        sets.append("api_key=?")
        params.append(encrypt_data((body["apiKey"]).strip()))
        
    if not sets: return error("nada para actualizar")
    params.extend([account_id, g.real_tg_id])
    
    db.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=? AND tg_id=?", params)
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})

@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def delete_account(account_id):
    db = get_db()
    cur = db.execute("DELETE FROM accounts WHERE id=? AND tg_id=?", (account_id, g.real_tg_id))
    db.commit()
    if cur.rowcount == 0: return error("cuenta no encontrada", 404)
    return jsonify({"ok": True})

@app.route("/api/accounts/<account_id>/mark-used", methods=["POST"])
def mark_used(account_id):
    db = get_db()
    from datetime import datetime, timezone
    iso_now = datetime.now(timezone.utc).isoformat()
    cur = db.execute("UPDATE accounts SET last_used=? WHERE id=? AND tg_id=?", (iso_now, account_id, g.real_tg_id))
    db.commit()
    if cur.rowcount == 0: return error("cuenta no encontrada", 404)
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})

@app.route("/api/accounts/<account_id>/toggle-enabled", methods=["POST"])
def toggle_enabled(account_id):
    db = get_db()
    row = db.execute("SELECT * FROM accounts WHERE id=? AND tg_id=?", (account_id, g.real_tg_id)).fetchone()
    if not row: return error("cuenta no encontrada", 404)
    db.execute("UPDATE accounts SET enabled=? WHERE id=?", (0 if row["enabled"] else 1, account_id))
    db.commit()
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return jsonify({"account": serialize_account(row)})

@app.route("/api/export")
@limiter.limit("10 per hour")
def export_data():
    db = get_db()
    profiles = db.execute("SELECT * FROM profiles WHERE tg_id=?", (g.real_tg_id,)).fetchall()
    accounts = db.execute("SELECT * FROM accounts WHERE tg_id=?", (g.real_tg_id,)).fetchall()
    return jsonify({
        "profiles": [serialize_profile(p) for p in profiles],
        "accounts": [serialize_account(a) for a in accounts],
    })

@app.route("/api/import", methods=["POST"])
@limiter.limit("5 per hour")
def import_data():
    body = request.get_json(force=True) or {}
    profiles_in, accounts_in = body.get("profiles") or [], body.get("accounts") or []
    mode = body.get("mode", "merge")

    db = get_db()
    if mode == "replace":
        db.execute("DELETE FROM accounts WHERE tg_id=?", (g.real_tg_id,))
        db.execute("DELETE FROM profiles WHERE tg_id=?", (g.real_tg_id,))

    id_map = {}
    for p in profiles_in:
        new_pid = new_id("p")
        id_map[p.get("id")] = new_pid
        db.execute(
            "INSERT INTO profiles (id, tg_id, name, created_at) VALUES (?,?,?,?)",
            (new_pid, g.real_tg_id, p.get("name") or "Sin nombre", p.get("createdAt") or now_ms()),
        )
    for a in accounts_in:
        mapped_pid = id_map.get(a.get("profileId"), a.get("profileId"))
        enc_key = encrypt_data((a.get("apiKey") or "").strip())
        db.execute(
            """INSERT INTO accounts
               (id, tg_id, profile_id, service, email, plan, limit_count, limit_reset_hours, last_used, notes, api_key, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id("a"), g.real_tg_id, mapped_pid,
                a.get("service") or "IA", a.get("email") or "", a.get("plan") or "Free",
                a.get("limitCount"), a.get("limitResetHours"), a.get("lastUsed") or "",
                a.get("notes") or "", enc_key, 1 if a.get("enabled", True) else 0,
                a.get("createdAt") or now_ms()
            )
        )
    db.commit()
    return jsonify({"ok": True, "profiles": len(profiles_in), "accounts": len(accounts_in)})

@app.route("/api/reset", methods=["DELETE"])
def reset_all():
    db = get_db()
    db.execute("DELETE FROM accounts WHERE tg_id=?", (g.real_tg_id,))
    db.execute("DELETE FROM profiles WHERE tg_id=?", (g.real_tg_id,))
    db.commit()
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# ⚡ Prompt Lab — mejora con IA (proxy seguro del lado del servidor)
# Pollinations ahora exige API key; acá vive como variable de entorno
# POLLINATIONS_KEY y nunca toca el navegador. Registrate en
# https://enter.pollinations.ai para obtenerla (tier gratuito disponible).
# ---------------------------------------------------------------------------
POLLINATIONS_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()

LAB_TARGET_LABELS = {
    "texto": "chat / asistente de texto",
    "imagen": "generación de imágenes",
    "musica": "generación de música (Suno)",
    "video": "generación de video",
    "codigo": "asistente de programación",
}

@app.route("/api/lab/enhance", methods=["POST"])
@limiter.limit("10 per minute")
def lab_enhance():
    if not POLLINATIONS_KEY:
        return error("IA online no configurada en el servidor (falta POLLINATIONS_KEY)", 503)

    body = request.get_json(force=True) or {}
    idea = (body.get("idea") or "").strip()
    target = body.get("target") or "texto"
    if not idea:
        return error("falta idea")
    if len(idea) > 4000:
        return error("idea demasiado larga (máx. 4000 caracteres)")

    target_label = LAB_TARGET_LABELS.get(target, LAB_TARGET_LABELS["texto"])
    metaprompt = (
        "Sos un ingeniero de prompts experto. Convertí la siguiente idea escrita en "
        f"lenguaje natural en un prompt avanzado, específico y profesional para una IA de tipo \"{target_label}\". "
        "El prompt resultante debe estar en español, ser directo, incluir contexto, requisitos "
        "concretos y formato de salida esperado. Respondé SOLO con el prompt final, sin "
        "explicaciones ni introducción.\n\nIDEA: " + idea
    )

    try:
        resp = requests.post(
            "https://gen.pollinations.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {POLLINATIONS_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai",
                "messages": [{"role": "user", "content": metaprompt}],
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return error(f"la IA respondió {resp.status_code}", 502)
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            return error("la IA devolvió una respuesta vacía", 502)
        return jsonify({"prompt": text})
    except requests.Timeout:
        return error("la IA tardó demasiado en responder", 504)
    except Exception as exc:
        return error(f"no se pudo contactar a la IA: {exc}", 502)

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
