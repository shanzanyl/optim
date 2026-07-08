# backend_web/app/ml.py
# Model LightGBM untuk Detection - Update path ke models/otdr/

import joblib
import json
import numpy as np
import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PATH - Model LightGBM untuk OTDR (folder models/otdr/)
# ══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent

# Cari model di folder models/otdr/
OTDR_MODEL_PATHS = [
    BASE_DIR / "models" / "otdr" / "lgbm_model.joblib",
    Path.cwd() / "models" / "otdr" / "lgbm_model.joblib",
]

OTDR_ENCODER_PATHS = [
    BASE_DIR / "models" / "otdr" / "label_encoder.joblib",
    Path.cwd() / "models" / "otdr" / "label_encoder.joblib",
]

OTDR_FEATURE_PATHS = [
    BASE_DIR / "models" / "otdr" / "feature_order.json",
    Path.cwd() / "models" / "otdr" / "feature_order.json",
]

OTDR_SCALER_PATHS = [
    BASE_DIR / "models" / "otdr" / "robust_scaler.joblib",
    Path.cwd() / "models" / "otdr" / "robust_scaler.joblib",
]

# ══════════════════════════════════════════════════════════════════
# LOAD MODEL
# ══════════════════════════════════════════════════════════════════

lgbm_model = None
label_encoder = None
robust_scaler = None
feature_columns = None

# Load model
for path in OTDR_MODEL_PATHS:
    if path.exists():
        try:
            lgbm_model = joblib.load(path)
            logger.info(f"✅ LightGBM model loaded: {path}")
            break
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")

# Load encoder
for path in OTDR_ENCODER_PATHS:
    if path.exists():
        try:
            label_encoder = joblib.load(path)
            logger.info(f"✅ Label encoder loaded: {path}")
            break
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")

# Load feature order
for path in OTDR_FEATURE_PATHS:
    if path.exists():
        try:
            with open(path, 'r') as f:
                feature_columns = json.load(f)
                feature_columns = [col.strip() for col in feature_columns]
            logger.info(f"✅ Feature order loaded: {path} ({len(feature_columns)} features)")
            break
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")

# Load robust scaler
for path in OTDR_SCALER_PATHS:
    if path.exists():
        try:
            robust_scaler = joblib.load(path)
            logger.info(f"✅ Robust scaler loaded: {path}")
            break
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")

# If model not found, create dummy feature columns
if feature_columns is None:
    feature_columns = [
        'Distance 1', 'Distance 2', 'Distance 3', 'Distance 4',
        'Loss 1', 'Loss 2', 'Loss 3',
        'Total-L 1', 'Total-L 2', 'Total-L 3', 'Total-L 4',
        'Avg-L 1', 'Avg-L 2', 'Avg-L 3', 'Avg-L 4',
        'Avg-Total', 'Return 1', 'Return 2', 'Return 3', 'Return 4'
    ]
    logger.warning("Using default feature columns")

# ══════════════════════════════════════════════════════════════════
# STATUS MAP
# ══════════════════════════════════════════════════════════════════

STATUS_MAP = {
    "Normal": "Normal",
    "Bending": "Warning",
    "Bad Splice": "Warning",
    "Air Gap": "Warning",
    "Dirty Connector": "Warning",
    "Nearly Cut": "Critical",
    "Fiber Cut": "Critical",
}

def get_status(label: str) -> str:
    return STATUS_MAP.get(label, "Warning")

# ══════════════════════════════════════════════════════════════════
# PREDICT FUNCTION (SAMA SEPERTI SEBELUMNYA)
# ══════════════════════════════════════════════════════════════════

def predict_from_otdr(otdr_values: dict) -> dict:
    """Prediksi menggunakan LightGBM model"""
    
    if lgbm_model is None:
        logger.warning("⚠️ Model not available, using rule-based prediction")
        
        # Rule-based prediction
        losses = [otdr_values.get('Loss 1', 0), otdr_values.get('Loss 2', 0), 
                  otdr_values.get('Loss 3', 0)]
        max_loss = max(losses) if losses else 0
        
        if max_loss > 3.0:
            prediction = "Nearly Cut"
            confidence = 85.0
        elif max_loss > 1.2:
            prediction = "Bending"
            confidence = 75.0
        elif max_loss > 0.5:
            prediction = "Bad Splice"
            confidence = 65.0
        else:
            prediction = "Normal"
            confidence = 90.0
        
        return {
            "prediction": prediction,
            "confidence": confidence,
            "status": get_status(prediction),
        }
    
    try:
        # Prepare features
        row = pd.DataFrame([otdr_values])
        
        # Ensure all columns exist.
        # Kolom yang tidak ada diisi NaN — bukan 0.0.
        # NaN adalah representasi yang benar untuk Fiber Cut (identik dengan training).
        for col in feature_columns:
            if col not in row.columns:
                row[col] = np.nan
        
        # Select and order features.
        # TIDAK menggunakan fillna() — NaN dipertahankan agar identik dengan
        # representasi Fiber Cut saat training. LightGBM mendukung NaN secara native.
        X = row[feature_columns].astype(np.float64)
        
        # Scale features
        if robust_scaler is not None:
            X = robust_scaler.transform(X)
        
        # Predict
        pred_encoded = lgbm_model.predict(X)[0]
        pred_int = int(round(pred_encoded)) if isinstance(pred_encoded, float) else int(pred_encoded)
        
        # Decode if encoder available
        if label_encoder is not None:
            label = label_encoder.inverse_transform([pred_int])[0]
        else:
            labels = ["Normal", "Bending", "Bad Splice", "Air Gap", "Dirty Connector", "Nearly Cut", "Fiber Cut"]
            label = labels[pred_int % len(labels)]
        
        # Get confidence
        confidence = 0.0
        if hasattr(lgbm_model, 'predict_proba'):
            proba = lgbm_model.predict_proba(X)[0]
            confidence = float(max(proba)) * 100
        
        logger.info(f"🤖 LightGBM → {label} (confidence: {confidence:.1f}%)")
        
        return {
            "prediction": str(label),
            "confidence": round(confidence, 2),
            "status": get_status(str(label)),
        }
        
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        import traceback
        traceback.print_exc()
        
        # Return rule-based fallback
        losses = [otdr_values.get('Loss 1', 0), otdr_values.get('Loss 2', 0), 
                  otdr_values.get('Loss 3', 0)]
        max_loss = max(losses) if losses else 0
        
        if max_loss > 3.0:
            prediction = "Nearly Cut"
        elif max_loss > 1.2:
            prediction = "Bending"
        elif max_loss > 0.5:
            prediction = "Bad Splice"
        else:
            prediction = "Normal"
        
        return {
            "prediction": prediction,
            "confidence": 70.0,
            "status": get_status(prediction),
        }