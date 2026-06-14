from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

telegram_alert_sent = Column(Boolean, default=False, server_default="false")

class User(Base):
    __tablename__ = "users"

    id       = Column(Integer, primary_key=True, index=True)  # ← primary key
    email    = Column(String(120), unique=True, nullable=False, index=True)
    password = Column(String(200), nullable=False)
    name     = Column(String(100), nullable=True)
    is_approved = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    results = relationship("OtdrResult", back_populates="owner", cascade="all, delete")


class OtdrResult(Base):
    __tablename__ = "otdr_results"

    id = Column(Integer, primary_key=True, index=True)  # ← PRIMARY KEY INI PENTING!
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Parameter umum
    prx         = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    wavelength  = Column(Float, nullable=True)
    pulse_width = Column(Float, nullable=True)

    # Distance per KM
    distance_1  = Column(Float, nullable=True)
    distance_2  = Column(Float, nullable=True)
    distance_3  = Column(Float, nullable=True)
    distance_4  = Column(Float, nullable=True)

    # Loss per KM
    loss_1      = Column(Float, nullable=True)
    loss_2      = Column(Float, nullable=True)
    loss_3      = Column(Float, nullable=True)
    loss_4      = Column(Float, nullable=True)

    # Total Loss per KM
    total_l_1   = Column(Float, nullable=True)
    total_l_2   = Column(Float, nullable=True)
    total_l_3   = Column(Float, nullable=True)
    total_l_4   = Column(Float, nullable=True)

    # Avg Loss per KM
    avg_l_1     = Column(Float, nullable=True)
    avg_l_2     = Column(Float, nullable=True)
    avg_l_3     = Column(Float, nullable=True)
    avg_l_4     = Column(Float, nullable=True)
    avg_total     = Column(Float, nullable=True)
    # Avg Total across all KM

    # Return Loss per KM
    return_1    = Column(Float, nullable=True)
    return_2    = Column(Float, nullable=True)
    return_3    = Column(Float, nullable=True)
    return_4    = Column(Float, nullable=True)

    # Hasil klasifikasi
    klasifikasi = Column(String(100), nullable=True)
    status      = Column(String(50), nullable=True)
    confidence  = Column(Float, nullable=True)

    # Metadata
    source      = Column(String(20), default="sheets")
    raw_text    = Column(Text, nullable=True)
    timestamp   = Column(DateTime(timezone=True), server_default=func.now())
    telegram_alert_sent = Column(Boolean, default=False, server_default="false")

    owner = relationship("User", back_populates="results")