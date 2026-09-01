from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os
import re
from urllib.parse import quote_plus

_base = os.environ.get("NEURO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_base, "brain_viewer.db")


def _azure_sql_url():
    """Build an Azure SQL URL out of the app settings App Service already has.

    The App Service is provisioned with AZURE_SQL_SERVER / AZURE_SQL_DATABASE /
    AZURE_SQL_AUTHENTICATION rather than one DATABASE_URL, so assemble the URL
    from those instead of carrying a duplicate setting. Returns None when they
    are absent, which is the normal desktop case.
    """
    server = os.environ.get("AZURE_SQL_SERVER")
    database = os.environ.get("AZURE_SQL_DATABASE") or os.environ.get("DATABASE_NAME")
    if not server or not database:
        return None

    auth = (os.environ.get("AZURE_SQL_AUTHENTICATION")
            or os.environ.get("DATABASE_AUTH_MODE") or "ActiveDirectoryMsi")
    parts = [
        "Driver={ODBC Driver 18 for SQL Server}",
        f"Server=tcp:{server},1433",
        f"Database={database}",
        f"Authentication={auth}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        # The serverless tier pauses after an idle period; the connection that
        # wakes it has to sit through the resume, which busts the 15 s default.
        "Connection Timeout=60",
    ]

    # Managed identity carries no credentials, and must not: the driver rejects
    # "Cannot use Authentication option with Integrated Security option".
    # Password modes put them in the ODBC string itself.
    if not auth.lower().startswith("activedirectorymsi"):
        user = os.environ.get("AZURE_SQL_USER") or ""
        if user:
            parts.append(f"UID={user}")
            parts.append(f"PWD={os.environ.get('AZURE_SQL_PASSWORD') or ''}")

    # odbc_connect passes the string to the driver verbatim. Spelling the URL
    # out the ordinary way instead makes SQLAlchemy's mssql dialect append
    # Trusted_Connection=Yes whenever no username is present -- which is always,
    # under managed identity -- and that is the Integrated Security the driver
    # refuses to pair with Authentication=.
    return "mssql+aioodbc:///?odbc_connect=" + quote_plus(";".join(parts))


# Precedence: an explicit DATABASE_URL wins, then the Azure app settings, then
# the local SQLite file next to the data root (desktop / dev).
DATABASE_URL = (os.environ.get("DATABASE_URL")
                or _azure_sql_url()
                or f"sqlite+aiosqlite:///{DB_PATH}")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# Managed databases drop idle connections and, on the serverless tier, pause
# entirely after inactivity -- the first request then hits a dead pooled
# connection. pre_ping validates before handing one out; recycle stays well
# under Azure SQL's ~30 min idle cutoff. Neither applies to a local file.
_engine_kwargs = {"echo": False}
if not IS_SQLITE:
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=300)

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# Mask credentials in both spellings: user:pass@host, and the PWD= field of
# an odbc_connect string (percent-encoded, so it survives quote_plus).
_shown = re.sub(r'://[^@]*@', '://***@', DATABASE_URL)
_shown = re.sub(r'(?i)(PWD(?:%3D|=))[^;%&]*', r'\1***', _shown)
print(f"[DB] Using database: {_shown}")


# NOTE ON STRING LENGTHS: every String column is explicitly sized. SQL Server
# maps an unlengthed String to varchar(max), which it refuses to use as an index
# key (error 1919) -- that breaks the unique constraints on users.username and
# reconstructions.share_token. SQLite ignores the lengths entirely.

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), default="viewer")
    created_at = Column(DateTime, default=datetime.utcnow)
    reconstructions = relationship("Reconstruction", back_populates="created_by_user")


class Reconstruction(Base):
    __tablename__ = "reconstructions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String(64), nullable=False)
    label = Column(String(255), nullable=False)
    share_token = Column(String(64), unique=True, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    mesh_path = Column(String(512), nullable=True)
    mri_path = Column(String(512), nullable=True)
    ct_path = Column(String(512), nullable=True)
    status = Column(String(32), default="pending")
    is_complete = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    registration_confirmed = Column(Boolean, default=False)
    # MNI export pipeline: none | exporting | exported | stale | error
    # "stale" = exported, but the reconstruction was unlocked for editing since
    export_status = Column(String(32), default="none")
    exported_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    created_by_user = relationship("User", back_populates="reconstructions")
    electrode_shafts = relationship("ElectrodeShaft", back_populates="reconstruction", cascade="all, delete-orphan")


class ElectrodeShaft(Base):
    __tablename__ = "electrode_shafts"
    id = Column(Integer, primary_key=True)
    reconstruction_id = Column(Integer, ForeignKey("reconstructions.id"))
    name = Column(String(64), nullable=False)            # short prefix e.g. "LA"
    label = Column(String(255), nullable=True)           # full label e.g. "Left Amygdala"
    electrode_type = Column(String(32), default="depth") # depth, strip, grid
    color = Column(String(16), default="#00ff88")
    visible = Column(Boolean, default=True)
    n_total_contacts = Column(Integer, default=12)
    spacing_mm = Column(Float, default=3.5)
    grid_rows = Column(Integer, nullable=True)
    grid_cols = Column(Integer, nullable=True)
    # Size parameters for 3D rendering
    contact_diameter_mm = Column(Float, default=0.8)  # macro sEEG default
    contact_length_mm = Column(Float, default=2.0)    # depth contact height
    shaft_diameter_mm = Column(Float, default=0.5)    # connecting rod
    contacts = relationship("ElectrodeContact", back_populates="shaft", cascade="all, delete-orphan")
    reconstruction = relationship("Reconstruction", back_populates="electrode_shafts")


class ElectrodeContact(Base):
    __tablename__ = "electrode_contacts"
    id = Column(Integer, primary_key=True)
    shaft_id = Column(Integer, ForeignKey("electrode_shafts.id"))
    contact_number = Column(Integer, nullable=False)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    z = Column(Float, nullable=False)
    x_mm = Column(Float, nullable=True)
    y_mm = Column(Float, nullable=True)
    z_mm = Column(Float, nullable=True)
    is_manual = Column(Boolean, default=True)
    shaft = relationship("ElectrodeShaft", back_populates="contacts")


class SeegRecording(Base):
    """
    An uploaded NeurosEEGRead HDF5 file, associated with a reconstruction.

    Fully parallel to the reconstruction pipeline: the file supplies named-channel
    activity, and the reconstruction supplies the electrode coordinates that the
    channels are joined to by name. Adding this table does not touch existing ones.
    """
    __tablename__ = "seeg_recordings"
    id = Column(Integer, primary_key=True)
    reconstruction_id = Column(Integer, ForeignKey("reconstructions.id"))
    task = Column(String(128), nullable=True)      # task key from the h5 (e.g. "word_repetition")
    filename = Column(String(255), nullable=False) # original upload filename
    stored_path = Column(String(512), nullable=False)  # path relative to DATA_DIR
    content_hash = Column(String(64), nullable=True)   # sha256 of the file, for upload dedup
    uploaded_at = Column(DateTime, default=datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] Tables created/verified")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
