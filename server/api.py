import asyncio
import io
import json
import logging
import os
import smtplib
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import bcrypt as _bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import Date, cast, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from db import AsyncSessionLocal, Base, engine, get_db
from detection_engine import DetectionEngine
from models import AgentModel, Alert, Event, PDFile, QuarantineFile, Screenshot, Privilege, User

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 8

# ── Логирование ───────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR  = os.path.join(_BASE_DIR, "logs")
_SCR_DIR  = os.path.join(_BASE_DIR, "screenshots")
_EMAIL_SETTINGS_FILE = os.path.join(_BASE_DIR, "email_settings.json")

os.makedirs(_LOG_DIR, exist_ok=True)
os.makedirs(_SCR_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_LOG_DIR, "server.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("edr.server")

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="School EDR — Защита ПД учеников")

BASE_DIR  = _BASE_DIR
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

detection_engine = DetectionEngine()
oauth2_scheme    = OAuth2PasswordBearer(tokenUrl="/auth/login")

# In-memory queue of manual scan requests: agent_id → [file_path, ...]
_pending_scans: dict[str, list[str]] = defaultdict(list)

app.mount("/screenshots", StaticFiles(directory=_SCR_DIR), name="screenshots")


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── Email helpers ─────────────────────────────────────────────────────────────

def _load_email_settings() -> dict:
    try:
        with open(_EMAIL_SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_email_settings(settings: dict):
    with open(_EMAIL_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _send_alert_email(alert_type: str, severity: str, description: str, agent_id: str):
    settings = _load_email_settings()
    if not settings.get("enabled") or not settings.get("director_email"):
        return
    try:
        host     = settings.get("smtp_host", "smtp.gmail.com")
        port     = int(settings.get("smtp_port", 587))
        user     = settings.get("smtp_user", "")
        password = settings.get("smtp_password", "")
        to_addr  = settings["director_email"]

        msg            = MIMEMultipart()
        msg["From"]    = user
        msg["To"]      = to_addr
        msg["Subject"] = f"[EDR] HIGH ALERT: {alert_type}"

        body = (
            f"EDR School — Критическое уведомление\n\n"
            f"Тип алерта: {alert_type}\n"
            f"Severity:   {severity}\n"
            f"Агент:      {agent_id}\n"
            f"Описание:   {description}\n"
            f"Время:      {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"Откройте дашборд: http://127.0.0.1:8088\n"
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            if user and password:
                s.login(user, password)
            s.sendmail(user, to_addr, msg.as_string())

        logger.info(f"Email алерт отправлен на {to_addr}")
    except Exception as e:
        logger.warning(f"Email отправка не удалась: {e}")


# ── WebSocket connection manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("=== EDR Server запускается ===")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                hashed_password=_hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
                role="admin",
            )
            db.add(admin)
            await db.commit()
            logger.info("Создан пользователь admin по умолчанию")

    # Fix orphaned PDFiles: is_blocked=True but no QuarantineFile record
    async with AsyncSessionLocal() as db:
        blocked_q = await db.execute(
            select(PDFile).where(PDFile.is_blocked == True)  # noqa: E712
        )
        for pd_file in blocked_q.scalars().all():
            existing = await db.execute(
                select(QuarantineFile).where(
                    QuarantineFile.agent_id == pd_file.agent_id,
                    QuarantineFile.original_path == pd_file.file_path,
                    QuarantineFile.is_restored == False,  # noqa: E712
                )
            )
            if not existing.scalars().first():
                qf = QuarantineFile(
                    agent_id=pd_file.agent_id,
                    original_path=pd_file.file_path,
                    quarantine_path="",
                    filename=pd_file.file_name,
                    pii_types=pd_file.pii_types,
                    confidence=pd_file.confidence,
                )
                db.add(qf)
                logger.info(f"Migration: created QuarantineFile for orphaned {pd_file.file_name}")
        await db.commit()

    # При старте сервера все агенты офлайн — они сами придут и зарегистрируются
    async with AsyncSessionLocal() as db:
        await db.execute(update(AgentModel).values(is_online=False))
        await db.commit()
        logger.info("Все агенты помечены офлайн до первого heartbeat")

    logger.info("EDR Server готов к работе")
    asyncio.create_task(_offline_watchdog())


async def _offline_watchdog():
    """Каждые 60 сек помечает агентов офлайн если нет heartbeat > 2 минут."""
    while True:
        await asyncio.sleep(60)
        try:
            cutoff = datetime.utcnow() - timedelta(seconds=120)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AgentModel).where(
                        AgentModel.is_online == True,       # noqa: E712
                        AgentModel.last_seen < cutoff,
                    )
                )
                stale = result.scalars().all()
                for agent in stale:
                    agent.is_online = False
                    logger.info(f"Агент офлайн (нет heartbeat): {agent.agent_id}")
                    await manager.broadcast(
                        {"type": "agent_update", "agent_id": agent.agent_id, "is_online": False}
                    )
                if stale:
                    await db.commit()
        except Exception as e:
            logger.error(f"_offline_watchdog: {e}")


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Недействительные учётные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise exc
    except JWTError:
        raise exc
    result = await db.execute(select(User).where(User.username == username))
    user   = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise exc
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return current_user


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class AgentRegisterIn(BaseModel):
    agent_id: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    os_info: Optional[str] = None


class EventIn(BaseModel):
    agent_id: str
    user: Optional[str] = None
    event_type: str
    severity: Optional[str] = None
    details: dict = {}


class PrivilegeIn(BaseModel):
    username: str
    agent_id: Optional[str] = None
    can_unlock_files: bool = False
    can_view_pii: bool = False


class QuarantineIn(BaseModel):
    agent_id: str
    original_path: str
    quarantine_path: str
    filename: Optional[str] = None
    pii_types: Optional[str] = None
    confidence: float = 0.0


class ScreenshotIn(BaseModel):
    agent_id: str
    event_type: Optional[str] = None
    filename: str
    image_b64: str


class EmailSettingsIn(BaseModel):
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    director_email: str = ""


# ── Endpoints: Auth ───────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form.username))
    user   = result.scalar_one_or_none()
    if not user or not _verify_password(form.password, user.hashed_password):
        logger.warning(f"Неудачная попытка входа: {form.username}")
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    logger.info(f"Успешный вход: {form.username}")
    return {"access_token": _create_token(user.username), "token_type": "bearer"}


@app.post("/auth/change-password")
async def change_password(
    data: ChangePasswordIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Новый пароль слишком короткий (минимум 6 символов)")
    current_user.hashed_password = _hash_password(data.new_password)
    await db.commit()
    logger.info(f"Пароль изменён: {current_user.username}")
    return {"status": "ok"}


# ── Endpoints: Agents ─────────────────────────────────────────────────────────

@app.post("/agents/register")
async def register_agent(data: AgentRegisterIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentModel).where(AgentModel.agent_id == data.agent_id)
    )
    agent = result.scalars().first()
    if agent:
        agent.hostname   = data.hostname   or agent.hostname
        agent.ip_address = data.ip_address or agent.ip_address
        agent.os_info    = data.os_info    or agent.os_info
        agent.last_seen  = datetime.utcnow()
        agent.is_online  = True
    else:
        agent = AgentModel(
            agent_id=data.agent_id,
            hostname=data.hostname,
            ip_address=data.ip_address,
            os_info=data.os_info,
        )
        db.add(agent)
        logger.info(f"Новый агент: {data.agent_id}")
    await db.commit()
    await manager.broadcast(
        {"type": "agent_update", "agent_id": data.agent_id, "is_online": True}
    )
    return {"status": "ok", "agent_id": data.agent_id}


@app.get("/agents")
async def list_agents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AgentModel).order_by(desc(AgentModel.last_seen)))
    agents = result.scalars().all()
    return [
        {
            "agent_id": a.agent_id, "hostname": a.hostname,
            "ip_address": a.ip_address, "os_info": a.os_info,
            "last_seen": a.last_seen, "is_online": a.is_online,
            "registered_at": a.registered_at,
        }
        for a in agents
    ]


# ── Endpoints: Events ─────────────────────────────────────────────────────────

@app.post("/events")
async def post_event(data: EventIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentModel).where(AgentModel.agent_id == data.agent_id)
    )
    agent = result.scalars().first()
    if agent:
        agent.last_seen = datetime.utcnow()
        agent.is_online = True
    else:
        agent = AgentModel(agent_id=data.agent_id)
        db.add(agent)

    event = Event(
        agent_id=data.agent_id, user=data.user,
        event_type=data.event_type, severity=data.severity,
        details=json.dumps(data.details, ensure_ascii=False),
    )
    db.add(event)

    if data.event_type in ("pii_detected", "file_open_authorized") \
            and data.details.get("file"):
        existing = await db.execute(
            select(PDFile).where(
                PDFile.agent_id == data.agent_id,
                PDFile.file_path == data.details["file"],
            )
        )
        if not existing.scalars().first():
            pd = PDFile(
                agent_id=data.agent_id,
                file_path=data.details["file"],
                file_name=data.details.get("file_name") or os.path.basename(data.details["file"]),
                pii_types=data.details.get("pii_types", ""),
                confidence=float(data.details.get("confidence", 0)),
                is_blocked=bool(data.details.get("blocked", False)),
            )
            db.add(pd)

    alert_data = detection_engine.process_event(
        agent_id=data.agent_id,
        event_type=data.event_type,
        details=data.details,
        severity=data.severity,
    )
    alert_obj: Alert | None = None
    if alert_data:
        alert_obj = Alert(**alert_data)
        db.add(alert_obj)

    await db.commit()
    logger.info(f"Событие: {data.event_type} [{data.severity}] от {data.agent_id}")

    if alert_obj and alert_obj.severity == "HIGH":
        import threading
        threading.Thread(
            target=_send_alert_email,
            args=(alert_obj.alert_type, alert_obj.severity,
                  alert_obj.description or "", data.agent_id),
            daemon=True,
        ).start()

    await manager.broadcast({
        "type": "event", "agent_id": data.agent_id,
        "event_type": data.event_type, "severity": data.severity,
        "user": data.user, "details": data.details,
        "timestamp": datetime.utcnow().isoformat(),
    })
    if alert_obj:
        await db.refresh(alert_obj)
        await manager.broadcast({
            "type": "alert", "id": alert_obj.id,
            "agent_id": alert_obj.agent_id,
            "alert_type": alert_obj.alert_type,
            "severity": alert_obj.severity,
            "description": alert_obj.description,
            "timestamp": datetime.utcnow().isoformat(),
        })

    return {"status": "ok"}


@app.get("/events")
async def get_events(
    severity: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Event).order_by(desc(Event.timestamp)).limit(min(limit, 10000))
    if severity:
        q = q.where(Event.severity == severity.upper())
    if agent_id:
        q = q.where(Event.agent_id == agent_id)
    result = await db.execute(q)
    return [
        {"id": e.id, "agent_id": e.agent_id, "user": e.user,
         "event_type": e.event_type, "severity": e.severity,
         "details": e.details, "timestamp": e.timestamp}
        for e in result.scalars().all()
    ]


# ── Endpoints: Alerts ─────────────────────────────────────────────────────────

@app.get("/alerts")
async def get_alerts(
    resolved: Optional[bool] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Alert).order_by(desc(Alert.timestamp)).limit(min(limit, 10000))
    if resolved is not None:
        q = q.where(Alert.is_resolved == resolved)
    if severity:
        q = q.where(Alert.severity == severity.upper())
    result = await db.execute(q)
    return [
        {"id": a.id, "agent_id": a.agent_id, "alert_type": a.alert_type,
         "severity": a.severity, "description": a.description,
         "is_resolved": a.is_resolved, "timestamp": a.timestamp}
        for a in result.scalars().all()
    ]


@app.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert  = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Алерт не найден")
    alert.is_resolved = True
    await db.commit()
    await manager.broadcast({"type": "alert_resolved", "id": alert_id})
    return {"status": "resolved"}


# ── Endpoints: PD Files count ─────────────────────────────────────────────────

@app.get("/pd-files/count")
async def pd_files_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(func.count()).select_from(PDFile))
    return {"count": result.scalar() or 0}


# ── Endpoints: Quarantine ─────────────────────────────────────────────────────

@app.post("/quarantine", summary="Агент регистрирует файл в карантине")
async def register_quarantine(
    data: QuarantineIn,
    db: AsyncSession = Depends(get_db),
):
    # Upsert by (agent_id, original_path)
    result = await db.execute(
        select(QuarantineFile).where(
            QuarantineFile.agent_id == data.agent_id,
            QuarantineFile.original_path == data.original_path,
            QuarantineFile.is_restored == False,
        )
    )
    existing = result.scalars().first()
    if existing:
        existing.quarantine_path = data.quarantine_path
        existing.timestamp       = datetime.utcnow()
    else:
        qf = QuarantineFile(
            agent_id=data.agent_id,
            original_path=data.original_path,
            quarantine_path=data.quarantine_path,
            filename=data.filename or os.path.basename(data.original_path),
            pii_types=data.pii_types,
            confidence=data.confidence,
        )
        db.add(qf)
    await db.commit()

    # Broadcast quarantine count update
    count_q = await db.execute(
        select(func.count()).select_from(QuarantineFile).where(
            QuarantineFile.is_restored == False
        )
    )
    await manager.broadcast({"type": "quarantine_update", "count": count_q.scalar() or 0})

    logger.info(f"Quarantine registered: {data.original_path}")
    return {"status": "ok"}


@app.get("/quarantine", summary="Список файлов в карантине")
async def list_quarantine(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QuarantineFile).order_by(desc(QuarantineFile.timestamp))
    )
    files = result.scalars().all()
    return [
        {
            "id": f.id, "agent_id": f.agent_id,
            "original_path": f.original_path,
            "quarantine_path": f.quarantine_path,
            "file_name": f.filename, "pii_types": f.pii_types,
            "confidence": f.confidence,
            "timestamp": f.timestamp, "is_restored": f.is_restored,
        }
        for f in files
    ]


@app.get("/quarantine/count", summary="Количество файлов в карантине")
async def quarantine_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count()).select_from(QuarantineFile).where(
            QuarantineFile.is_restored == False
        )
    )
    return {"count": result.scalar() or 0}


@app.post("/quarantine/{file_id}/restore", summary="Восстановить файл из карантина")
async def restore_quarantine(
    file_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QuarantineFile).where(QuarantineFile.id == file_id))
    qf = result.scalar_one_or_none()
    if not qf:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if qf.is_restored:
        raise HTTPException(status_code=400, detail="Файл уже восстановлен")

    # Файл карантина находится на агенте (ноутбук 2), не на сервере.
    # Ставим флаг — агент сам выполнит restore при следующем polling.
    qf.pending_restore = True
    await db.commit()
    logger.info(f"Восстановление запрошено: {qf.filename} by {current_user.username}")
    return {"status": "ok", "message": "Команда восстановления отправлена агенту"}


@app.get("/quarantine/pending-restore")
async def pending_restore(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Агент спрашивает: есть ли файлы для восстановления?"""
    result = await db.execute(
        select(QuarantineFile).where(
            # Включаем файлы этого агента + файлы без agent_id (старые записи)
            or_(QuarantineFile.agent_id == agent_id, QuarantineFile.agent_id.is_(None)),
            QuarantineFile.pending_restore == True,  # noqa: E712
            QuarantineFile.is_restored == False,      # noqa: E712
        )
    )
    files = result.scalars().all()
    return [
        {"id": f.id, "quarantine_path": f.quarantine_path, "original_path": f.original_path}
        for f in files
    ]


@app.post("/quarantine/{file_id}/restore-done")
async def restore_done(
    file_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Агент сообщает что восстановление выполнено."""
    result = await db.execute(select(QuarantineFile).where(QuarantineFile.id == file_id))
    qf = result.scalar_one_or_none()
    if not qf:
        raise HTTPException(status_code=404, detail="Файл не найден")
    qf.pending_restore = False
    if data.get("success"):
        qf.is_restored = True
        logger.info(f"Файл восстановлен агентом: {qf.filename}")
        # Сбросить флаг is_blocked в PDFile, чтобы файл снова появился как активный
        pd_result = await db.execute(
            select(PDFile).where(PDFile.file_path == qf.original_path)
        )
        pd_file = pd_result.scalars().first()
        if pd_file:
            pd_file.is_blocked = False
            pd_file.pending_quarantine = False
    await db.commit()
    await manager.broadcast({"type": "quarantine_restored", "id": file_id})
    await manager.broadcast({"type": "pd_files_update"})
    return {"status": "ok"}


@app.delete("/quarantine/{file_id}", summary="Удалить файл из карантина навсегда")
async def delete_quarantine(
    file_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QuarantineFile).where(QuarantineFile.id == file_id))
    qf     = result.scalar_one_or_none()
    if not qf:
        raise HTTPException(status_code=404, detail="Файл не найден")

    # Delete from disk if still exists
    if os.path.isfile(qf.quarantine_path):
        try:
            os.remove(qf.quarantine_path)
            logger.info(f"Permanently deleted: {qf.quarantine_path}")
        except Exception as e:
            logger.warning(f"Cannot delete {qf.quarantine_path}: {e}")

    # Remove stub too
    if os.path.isfile(qf.original_path):
        try:
            with open(qf.original_path, "r", encoding="utf-8", errors="ignore") as f:
                if "[EDR-QUARANTINE]" in f.read(300):
                    os.remove(qf.original_path)
        except Exception:
            pass

    await db.delete(qf)
    await db.commit()

    await manager.broadcast({"type": "quarantine_deleted", "id": file_id})
    return {"status": "deleted"}


# ── Endpoints: Screenshots ────────────────────────────────────────────────────

@app.post("/screenshots")
async def upload_screenshot(data: ScreenshotIn, db: AsyncSession = Depends(get_db)):
    import base64
    try:
        img_bytes = base64.b64decode(data.image_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Неверный base64")

    scr_path = os.path.join(_SCR_DIR, data.filename)
    with open(scr_path, "wb") as f:
        f.write(img_bytes)

    scr = Screenshot(agent_id=data.agent_id, event_type=data.event_type, filename=data.filename)
    db.add(scr)
    await db.commit()
    return {"status": "ok", "filename": data.filename}


# ── Endpoints: Statistics ─────────────────────────────────────────────────────

@app.get("/statistics")
async def get_statistics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now      = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    by_day_q = await db.execute(
        select(
            cast(Event.timestamp, Date).label("day"),
            Event.severity,
            func.count(Event.id).label("cnt"),
        )
        .where(Event.timestamp >= week_ago)
        .group_by(cast(Event.timestamp, Date), Event.severity)
        .order_by(cast(Event.timestamp, Date))
    )
    days_map: dict = {}
    for row in by_day_q.all():
        day = str(row.day)
        if day not in days_map:
            days_map[day] = {"date": day, "LOW": 0, "MEDIUM": 0, "HIGH": 0}
        sev = row.severity or "LOW"
        if sev in days_map[day]:
            days_map[day][sev] = row.cnt

    by_type_q = await db.execute(
        select(Event.event_type, func.count(Event.id).label("cnt"))
        .group_by(Event.event_type)
        .order_by(desc(func.count(Event.id)))
        .limit(10)
    )
    by_type = {row.event_type: row.cnt for row in by_type_q.all()}

    by_agent_q = await db.execute(
        select(Event.agent_id, func.count(Event.id).label("cnt"))
        .group_by(Event.agent_id)
        .order_by(desc(func.count(Event.id)))
        .limit(5)
    )
    by_agent = {(row.agent_id or "unknown"): row.cnt for row in by_agent_q.all()}

    total_q  = await db.execute(select(func.count()).select_from(Event))
    total    = total_q.scalar() or 0
    high_q   = await db.execute(select(func.count()).select_from(Event).where(Event.severity == "HIGH"))
    high_cnt = high_q.scalar() or 0
    med_q    = await db.execute(select(func.count()).select_from(Event).where(Event.severity == "MEDIUM"))
    med_cnt  = med_q.scalar() or 0

    quar_q   = await db.execute(
        select(func.count()).select_from(QuarantineFile).where(QuarantineFile.is_restored == False)
    )
    quar_cnt = quar_q.scalar() or 0

    return {
        "total": total, "high": high_cnt, "medium": med_cnt,
        "low": max(total - high_cnt - med_cnt, 0),
        "quarantine": quar_cnt,
        "by_day": list(days_map.values()),
        "by_type": by_type, "by_agent": by_agent,
    }


# ── Endpoints: Reports ────────────────────────────────────────────────────────

def _generate_pdf_report(title: str, events: list, from_dt: datetime, to_dt: datetime) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm

    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               rightMargin=2*cm, leftMargin=2*cm,
                               topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story  = []

    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    story.append(Paragraph(f"EDR School — {title}", title_style))
    story.append(Paragraph(
        f"Период: {from_dt.strftime('%d.%m.%Y')} — {to_dt.strftime('%d.%m.%Y')}",
        styles["Normal"],
    ))
    story.append(Paragraph(
        f"Сформирован: {datetime.utcnow().strftime('%d.%m.%Y %H:%M')} UTC",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.5*cm))

    high_cnt   = sum(1 for e in events if e.get("severity") == "HIGH")
    medium_cnt = sum(1 for e in events if e.get("severity") == "MEDIUM")
    low_cnt    = sum(1 for e in events if e.get("severity") == "LOW")

    summary_data = [
        ["Показатель", "Значение"],
        ["Всего событий", str(len(events))],
        ["HIGH события",   str(high_cnt)],
        ["MEDIUM события", str(medium_cnt)],
        ["LOW события",    str(low_cnt)],
    ]
    st = Table(summary_data, colWidths=[10*cm, 5*cm])
    st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    story.append(st)
    story.append(Spacer(1, 0.5*cm))

    story.append(Paragraph("Список событий", styles["Heading2"]))
    ev_data = [["Время", "Агент", "Тип события", "Severity"]]
    for ev in events[-50:]:
        ts = ev.get("timestamp", "")
        ts = ts.strftime("%d.%m %H:%M") if hasattr(ts, "strftime") else str(ts)[:16]
        ev_data.append([
            ts,
            str(ev.get("agent_id") or "")[:20],
            str(ev.get("event_type") or "")[:30],
            ev.get("severity") or "",
        ])

    if len(ev_data) > 1:
        sev_colors = {"HIGH": colors.HexColor("#f8d7da"),
                      "MEDIUM": colors.HexColor("#fff3cd"),
                      "LOW": colors.HexColor("#d1e7dd")}
        et = Table(ev_data, colWidths=[3.5*cm, 4*cm, 7*cm, 2.5*cm])
        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("GRID",       (0, 0), (-1, -1), 0.3, colors.grey),
        ]
        for i, row in enumerate(ev_data[1:], 1):
            if row[3] in sev_colors:
                cmds.append(("BACKGROUND", (0, i), (-1, i), sev_colors[row[3]]))
        et.setStyle(TableStyle(cmds))
        story.append(et)

    doc.build(story)
    return buf.getvalue()


@app.get("/reports/weekly")
async def report_weekly(
    db: AsyncSession = Depends(get_db),
):
    now     = datetime.utcnow()
    from_dt = now - timedelta(days=7)
    result  = await db.execute(
        select(Event).where(Event.timestamp >= from_dt).order_by(Event.timestamp)
    )
    events = [
        {"timestamp": e.timestamp, "agent_id": e.agent_id,
         "event_type": e.event_type, "severity": e.severity}
        for e in result.scalars().all()
    ]
    pdf   = _generate_pdf_report("Еженедельный отчёт", events, from_dt, now)
    fname = f"edr_weekly_{now.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/reports/monthly")
async def report_monthly(
    db: AsyncSession = Depends(get_db),
):
    now     = datetime.utcnow()
    from_dt = now - timedelta(days=30)
    result  = await db.execute(
        select(Event).where(Event.timestamp >= from_dt).order_by(Event.timestamp)
    )
    events = [
        {"timestamp": e.timestamp, "agent_id": e.agent_id,
         "event_type": e.event_type, "severity": e.severity}
        for e in result.scalars().all()
    ]
    pdf   = _generate_pdf_report("Ежемесячный отчёт", events, from_dt, now)
    fname = f"edr_monthly_{now.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Endpoints: Email settings ─────────────────────────────────────────────────

@app.get("/settings/email")
async def get_email_settings(current_user: User = Depends(require_admin)):
    s = _load_email_settings()
    s.pop("smtp_password", None)
    return s


@app.post("/settings/email")
async def save_email_settings(
    data: EmailSettingsIn,
    current_user: User = Depends(require_admin),
):
    existing = _load_email_settings()
    _save_email_settings({
        "enabled":        data.enabled,
        "smtp_host":      data.smtp_host,
        "smtp_port":      data.smtp_port,
        "smtp_user":      data.smtp_user,
        "director_email": data.director_email,
        "smtp_password":  data.smtp_password or existing.get("smtp_password", ""),
    })
    return {"status": "ok"}


@app.post("/settings/email/test")
async def test_email(current_user: User = Depends(require_admin)):
    settings = _load_email_settings()
    if not settings.get("director_email"):
        raise HTTPException(status_code=400, detail="Email получателя не настроен")
    import threading
    threading.Thread(
        target=_send_alert_email,
        args=("TEST_ALERT", "HIGH", "Тестовое уведомление от EDR School", "server"),
        daemon=True,
    ).start()
    return {"status": "ok"}


# ── Endpoints: Privileges ─────────────────────────────────────────────────────

@app.post("/privileges")
async def create_privilege(
    data: PrivilegeIn,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == data.username))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    priv = Privilege(
        user_id=target.id, agent_id=data.agent_id,
        can_unlock_files=data.can_unlock_files,
        can_view_pii=data.can_view_pii,
        granted_by=current_user.username,
    )
    db.add(priv)
    await db.commit()
    return {"status": "ok"}


@app.get("/privileges")
async def get_privileges(
    username: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Privilege)
    if username:
        user_res = await db.execute(select(User).where(User.username == username))
        user     = user_res.scalar_one_or_none()
        if not user:
            return []
        q = q.where(Privilege.user_id == user.id)
    result    = await db.execute(q)
    privs     = result.scalars().all()
    user_ids  = list({p.user_id for p in privs})
    users_res = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_map = {u.id: u.username for u in users_res.scalars().all()}
    return [
        {
            "id": p.id, "username": users_map.get(p.user_id, "unknown"),
            "agent_id": p.agent_id, "can_unlock_files": p.can_unlock_files,
            "can_view_pii": p.can_view_pii, "granted_by": p.granted_by,
            "granted_at": p.granted_at,
        }
        for p in privs
    ]


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── PD-Files: список + карантин по команде ───────────────────────────────────

class QuarantineDoneIn(BaseModel):
    quarantine_path: str = ""
    success: bool = False


@app.get("/pd-files")
async def list_pd_files(
    agent_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(PDFile).order_by(desc(PDFile.detected_at))
    if agent_id:
        q = q.where(PDFile.agent_id == agent_id)
    result = await db.execute(q)
    files = result.scalars().all()
    return [
        {
            "id": f.id,
            "agent_id": f.agent_id,
            "file_path": f.file_path,
            "file_name": f.file_name,
            "pii_types": f.pii_types,
            "confidence": f.confidence,
            "is_blocked": f.is_blocked,
            "pending_quarantine": f.pending_quarantine,
            "detected_at": str(f.detected_at),
        }
        for f in files
    ]


@app.post("/pd-files/{file_id}/request-quarantine")
async def request_quarantine(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PDFile).where(PDFile.id == file_id))
    pd_file = result.scalar_one_or_none()
    if not pd_file:
        raise HTTPException(status_code=404, detail="File not found")
    if pd_file.is_blocked:
        return {"status": "already_quarantined"}
    pd_file.pending_quarantine = True
    await db.commit()
    logger.info(f"Карантин запрошен: {pd_file.file_name} (агент {pd_file.agent_id})")
    return {"status": "ok"}


@app.get("/pd-files/pending-quarantine")
async def pending_quarantine(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Агент спрашивает: есть ли файлы для карантина? Авторизация не нужна."""
    result = await db.execute(
        select(PDFile).where(
            PDFile.agent_id == agent_id,
            PDFile.pending_quarantine == True,  # noqa: E712
            PDFile.is_blocked == False,          # noqa: E712
        )
    )
    files = result.scalars().all()
    return [{"id": f.id, "file_path": f.file_path} for f in files]


@app.post("/pd-files/{file_id}/quarantine-done")
async def quarantine_done(
    file_id: int,
    data: QuarantineDoneIn,
    db: AsyncSession = Depends(get_db),
):
    """Агент сообщает что карантин выполнен."""
    result = await db.execute(select(PDFile).where(PDFile.id == file_id))
    pd_file = result.scalar_one_or_none()
    if not pd_file:
        raise HTTPException(status_code=404, detail="File not found")

    pd_file.pending_quarantine = False
    if data.success:
        pd_file.is_blocked = True
        # Always create/update QuarantineFile so the quarantine tab shows the file.
        existing = await db.execute(
            select(QuarantineFile).where(
                QuarantineFile.agent_id == pd_file.agent_id,
                QuarantineFile.original_path == pd_file.file_path,
                QuarantineFile.is_restored == False,  # noqa: E712
            )
        )
        qf_existing = existing.scalars().first()
        if qf_existing:
            if data.quarantine_path:
                qf_existing.quarantine_path = data.quarantine_path
        else:
            qf = QuarantineFile(
                agent_id=pd_file.agent_id,
                original_path=pd_file.file_path,
                quarantine_path=data.quarantine_path or "",
                filename=pd_file.file_name,
                pii_types=pd_file.pii_types,
                confidence=pd_file.confidence,
            )
            db.add(qf)
        logger.info(f"Файл в карантине: {pd_file.file_name} path={data.quarantine_path!r}")

    await db.commit()
    if data.success:
        cnt = await db.execute(
            select(func.count()).select_from(QuarantineFile).where(QuarantineFile.is_restored == False)  # noqa: E712
        )
        await manager.broadcast({"type": "quarantine_update", "count": cnt.scalar() or 0})
    return {"status": "ok"}


# ── Manual file scan ─────────────────────────────────────────────────────────

class ScanFileIn(BaseModel):
    agent_id: str
    file_name: str   # filename or partial name to search across all drives


class ScanFileDoneIn(BaseModel):
    agent_id: str
    file_path: str
    is_pii: bool
    pii_types: str = ""
    confidence: float = 0.0
    found_count: int = 0   # how many matching files were found on disk


@app.post("/scan-file")
async def scan_file_request(
    data: ScanFileIn,
    current_user: User = Depends(get_current_user),
):
    """Dashboard asks agent to search for a file by name and scan it."""
    _pending_scans[data.agent_id].append(data.file_name)
    logger.info(f"Запрос поиска+скан: '{data.file_name}' (агент {data.agent_id})")
    return {"status": "queued"}


@app.get("/pending-scans")
async def get_pending_scans(agent_id: str):
    """Agent polls: which filenames should I search and scan? No auth needed."""
    names = _pending_scans.pop(agent_id, [])
    return [{"name": n} for n in names]


@app.post("/scan-file-done")
async def scan_file_done(
    data: ScanFileDoneIn,
    db: AsyncSession = Depends(get_db),
):
    """Agent reports scan result. If PII found, creates/updates PDFile."""
    if data.is_pii:
        fname = os.path.basename(data.file_path)
        existing = await db.execute(
            select(PDFile).where(
                PDFile.agent_id == data.agent_id,
                PDFile.file_path == data.file_path,
            )
        )
        pd = existing.scalars().first()
        if not pd:
            pd = PDFile(
                agent_id=data.agent_id,
                file_path=data.file_path,
                file_name=fname,
                pii_types=data.pii_types,
                confidence=data.confidence,
            )
            db.add(pd)
            await db.commit()
        await manager.broadcast({"type": "pd_files_update"})
    await manager.broadcast({
        "type": "scan_file_done",
        "file_path": data.file_path,
        "is_pii": data.is_pii,
        "pii_types": data.pii_types,
        "confidence": data.confidence,
        "found_count": data.found_count,
    })
    logger.info(f"Результат скана {data.file_path}: is_pii={data.is_pii}")
    return {"status": "ok"}


# ── Dashboard / Health ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})
