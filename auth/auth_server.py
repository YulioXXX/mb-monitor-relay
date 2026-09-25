# auth/auth_server.py
# Servidor de autenticación — corre en Render.com (servicio separado al relay)
# Gestiona empresas, credenciales y emisión de tokens JWT.

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import bcrypt
import jwt
import uuid
import os
from datetime import datetime, timedelta

app = FastAPI(title="Monitor Auth Server", version="1.0")

# Variables de entorno (configurar en Render)
JWT_SECRET = os.environ.get("JWT_SECRET", "cambiar_en_produccion_32chars_min")
ADMIN_KEY  = os.environ.get("ADMIN_KEY",  "clave_admin_secreta")
DB_PATH    = os.environ.get("DB_PATH",    "companies.db")
TOKEN_DAYS = 30  # días de validez del token


# ─── BASE DE DATOS ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id   TEXT    UNIQUE NOT NULL,
            name         TEXT    NOT NULL,
            email        TEXT    UNIQUE NOT NULL,
            password_hash TEXT   NOT NULL,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_db()


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
    payload = {
        "company_id": company_id,
        "name":       name,
        "email":      email,
        "exp":        datetime.utcnow() + timedelta(days=TOKEN_DAYS),
        "iat":        datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_token(token: str) -> dict:
    """Valida un token JWT. Lanza excepción si es inválido o expirado."""
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


# ─── ENDPOINTS PÚBLICOS ───────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Monitor Auth Server v1.0"}


@app.post("/auth/login")
def login(data: LoginRequest):
    """
    Inicio de sesión para empresas cliente.
    Retorna un token JWT válido por 30 días.
    """
    conn = get_db()
    row  = conn.execute(
        "SELECT company_id, name, password_hash, is_active "
        "FROM companies WHERE email = ?",
        (data.email.strip().lower(),)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Cuenta suspendida. Contacte al proveedor.")

    if not bcrypt.checkpw(data.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = make_token(row["company_id"], row["name"], data.email)

    return {
        "token":        token,
        "company_id":   row["company_id"],
        "company_name": row["name"],
    }


# ─── ENDPOINTS DE ADMINISTRACIÓN (solo para Julio) ───────────────────────────

@app.post("/admin/company/create")
def create_company(data: CreateCompanyRequest):
    """
    Crea una nueva empresa cliente.
    Requiere ADMIN_KEY (solo el proveedor puede hacer esto).
    """
    if data.admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave de administrador incorrecta")

    company_id    = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO companies (company_id, name, email, password_hash) "
            "VALUES (?, ?, ?, ?)",
            (company_id, data.name, data.email.strip().lower(), password_hash)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")
    conn.close()

    return {
        "message":    f"Empresa '{data.name}' creada correctamente.",
        "company_id": company_id,
        "email":      data.email,
        "nota":       f"Usar company_id='{company_id}' en shared/config.py al compilar el agente de esta empresa.",
    }


@app.get("/admin/companies")
def list_companies(admin_key: str):
    """Lista todas las empresas registradas."""
    if admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave incorrecta")

    conn = get_db()
    rows = conn.execute(
        "SELECT company_id, name, email, is_active, created_at "
        "FROM companies ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    return [dict(r) for r in rows]


@app.post("/admin/company/toggle")
def toggle_company(data: ToggleCompanyRequest):
    """Activa o suspende una empresa."""
    if data.admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Clave incorrecta")

    conn = get_db()
    conn.execute(
        "UPDATE companies SET is_active = ? WHERE company_id = ?",
        (1 if data.is_active else 0, data.company_id)
    )
    conn.commit()
    conn.close()

    estado = "activada" if data.is_active else "suspendida"
    return {"message": f"Empresa {data.company_id} {estado}."}
