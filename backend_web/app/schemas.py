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