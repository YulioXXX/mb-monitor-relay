# auth/auth_server.py  v2
# Usa PostgreSQL (Supabase) en vez de SQLite — persiste entre deploys de Render.

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
import psycopg2.extras
import bcrypt
import jwt
import uuid
import os
from datetime import datetime, timedelta

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Monitor Auth Server", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JWT_SECRET  = os.environ.get("JWT_SECRET",  "cambiar_en_produccion_32chars_min")
ADMIN_KEY   = os.environ.get("ADMIN_KEY",   "clave_admin_secreta")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TOKEN_DAYS  = 30


# ─── BASE DE DATOS ────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id            SERIAL PRIMARY KEY,
            company_id    TEXT UNIQUE NOT NULL,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_active     BOOLEAN DEFAULT TRUE,
            created_at    TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


try:
    init_db()
except Exception as e:
    print(f"[WARN] No se pudo inicializar la BD: {e}")


# ─── MODELOS ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    str
    password: str

class CreateCompanyRequest(BaseModel):
    name:      str
    email:     str
    password:  str
    admin_key: str

class ToggleCompanyRequest(BaseModel):
    company_id: str
    is_active:  bool
    admin_key:  str


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def make_token(company_id: str, name: str, email: str) -> str:
    return jwt.encode({
        "company_id": company_id,
        "name":       name,
        "email":      email,
        "exp":        datetime.utcnow() + timedelta(days=TOKEN_DAYS),
        "iat":        datetime.utcnow(),
    }, JWT_SECRET, algorithm="HS256")


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0"}


@app.post("/auth/login")
def login(data: LoginRequest):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        "SELECT company_id, name, password_hash, is_active "
        "FROM companies WHERE email = %s",
        (data.email.strip().lower(),)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Cuenta suspendida. Contacte al proveedor.")
    if not bcrypt.checkpw(data.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    return {
        "token":        make_token(row["company_id"], row["name"], data.email),
        "company_id":   row["company_id"],
        "company_name": row["name"],
    }


@app.post("/admin/company/create")
def create_company(data: CreateCompanyRequest):
    if data.admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave de administrador incorrecta")

    company_id    = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO companies (company_id, name, email, password_hash) "
            "VALUES (%s, %s, %s, %s)",
            (company_id, data.name, data.email.strip().lower(), password_hash)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")
    cur.close()
    conn.close()

    return {
        "message":    f"Empresa '{data.name}' creada correctamente.",
        "company_id": company_id,
        "email":      data.email,
        "nota":       f"Usar company_id='{company_id}' en shared/config.py al compilar el agente.",
    }


@app.get("/admin/companies")
def list_companies(admin_key: str):
    if admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave incorrecta")

    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        "SELECT company_id, name, email, is_active, created_at "
        "FROM companies ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/admin/company/toggle")
def toggle_company(data: ToggleCompanyRequest):
    if data.admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave incorrecta")

    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        "UPDATE companies SET is_active = %s WHERE company_id = %s",
        (data.is_active, data.company_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    estado = "activada" if data.is_active else "suspendida"
    return {"message": f"Empresa {data.company_id} {estado}."}
