from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# ── Auth ──────────────────────────────────────────────
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    is_admin: bool = False
    is_approved: bool = False

    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Admin ──────────────────────────────────────────────
class UserAdminView(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    is_admin: bool
    is_approved: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── OTDR Result ────────────────────────────────────────
class OtdrResultOut(BaseModel):
    id: int
    loss: Optional[float] = None
    return_loss: Optional[float] = None
    jarak: Optional[float] = None
    klasifikasi: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Prediction ─────────────────────────────────────────
class PredictionItem(BaseModel):
    loss: float
    return_loss: float
    jarak: float
    prediction: str
    confidence: Optional[float] = None
    status: str

class PredictionResponse(BaseModel):
    message: str
    results: List[PredictionItem]
    total: int


# ── OCR ───────────────────────────────────────────────
class OcrResponse(BaseModel):
    message: str
    raw_text: str
    extracted: dict
    prediction: Optional[str] = None
    status: Optional[str] = None


# ── Manual Classification ──────────────────────────────
class ManualClassifyRequest(BaseModel):
    prx: float
    distance_1: float
    distance_2: float
    distance_3: float
    distance_4: float
    loss_1: float
    loss_2: float
    loss_3: float
    loss_4: float
    total_l_1: float
    total_l_2: float
    total_l_3: float
    total_l_4: float
    avg_l_1: float
    avg_l_2: float
    avg_l_3: float
    avg_l_4: float
    avg_total: float
    return_1: float
    return_2: float
    return_3: float
    return_4: float