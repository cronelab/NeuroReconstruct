from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import os

_base = os.environ.get("NEURO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_base, "brain_viewer.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

print(f"[DB] Using database at: {DB_PATH}")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer")
    created_at = Column(DateTime, default=datetime.utcnow)
    reconstructions = relationship("Reconstruction", back_populates="created_by_user")


class Reconstruction(Base):
    __tablename__ = "reconstructions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False)
    label = Column(String, nullable=False)
    share_token = Column(String, unique=True, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    mesh_path = Column(String, nullable=True)
    mri_path = Column(String, nullable=True)
    ct_path = Column(String, nullable=True)
    status = Column(String, default="pending")
    is_complete = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)
    created_by_user = relationship("User", back_populates="reconstructions")
    electrode_shafts = relationship("ElectrodeShaft", back_populates="reconstruction", cascade="all, delete-orphan")


class ElectrodeShaft(Base):
    __tablename__ = "electrode_shafts"
    id = Column(Integer, primary_key=True)
    reconstruction_id = Column(Integer, ForeignKey("reconstructions.id"))
    name = Column(String, nullable=False)           # short prefix e.g. "LA"
    label = Column(String, nullable=True)            # full label e.g. "Left Amygdala"
    electrode_type = Column(String, default="depth") # depth, strip, grid
    color = Column(String, default="#00ff88")
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


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] Tables created/verified")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
