# backend_web/app/ml_sor.py
# Model Random Forest untuk Dashboard SOR

import joblib
import json
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PATH - Model Random Forest untuk SOR
# ══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent

# Cari model di folder models/sor/
SOR_MODEL_PATHS = [
    BASE_DIR / "models" / "sor" / "random_forest_model.pkl",
    Path.cwd() / "models" / "sor" / "random_forest_model.pkl",
]

SOR_SCALER_PATHS = [
    BASE_DIR / "models" / "sor" / "scaler.pkl",
    Path.cwd() / "models" / "sor" / "scaler.pkl",
]

SOR_FEATURE_PATHS = [
    BASE_DIR / "models" / "sor" / "feature_names.json",
    Path.cwd() / "models" / "sor" / "feature_names.json",
]

SOR_LABEL_PATHS = [
    BASE_DIR / "models" / "sor" / "label_encoder_classes.json",
    Path.cwd() / "models" / "sor" / "label_encoder_classes.json",
]

# ══════════════════════════════════════════════════════════════════
# LOAD MODEL
# ══════════════════════════════════════════════════════════════════

sor_model = None
sor_scaler = None
sor_feature_names = None
sor_label_classes = None

def load_sor_models():
    """Load semua model Random Forest untuk SOR"""
    global sor_model, sor_scaler, sor_feature_names, sor_label_classes
    
    logger.info("=" * 50)
    logger.info("🔄 Loading SOR Random Forest models...")
    
    # Load model
    for path in SOR_MODEL_PATHS:
        if path.exists():
            try:
                sor_model = joblib.load(path)
                logger.info(f"✅ SOR model loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    
    # Load scaler
    for path in SOR_SCALER_PATHS:
        if path.exists():
            try:
                sor_scaler = joblib.load(path)
                logger.info(f"✅ SOR scaler loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    
    # Load feature names
    for path in SOR_FEATURE_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_feature_names = json.load(f)
                logger.info(f"✅ SOR feature names loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    
    # Load label classes
    for path in SOR_LABEL_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_label_classes = json.load(f)
                logger.info(f"✅ SOR label classes loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    
    if sor_model is None:
        logger.warning("⚠️ SOR model not found! Please check file paths.")
    
    logger.info("=" * 50)
    return sor_model is not None

def predict_sor_window(window_data: list) -> dict:
    """
    Prediksi satu window dengan Random Forest
    
    Args:
        window_data: list of 128 backscatter values
    
    Returns:
        dict: {prediction, confidence}
    """
    if sor_model is None:
        raise Exception("SOR model not loaded")
    
    # Konversi ke numpy array
    X = np.array(window_data).reshape(1, -1)
    
    # Normalisasi (jika ada scaler)
    if sor_scaler is not None:
        try:
            X = sor_scaler.transform(X)
        except Exception as e:
            logger.warning(f"Scaler transform error: {e}")
    
    # Prediksi
    pred = sor_model.predict(X)[0]
    
    # Confidence
    confidence = 95.0
    if hasattr(sor_model, 'predict_proba'):
        proba = sor_model.predict_proba(X)[0]
        confidence = float(np.max(proba) * 100)
    
    # Decode label
    if sor_label_classes is not None:
        if isinstance(sor_label_classes, list):
            if isinstance(pred, (int, np.integer)):
                pred_label = sor_label_classes[pred] if pred < len(sor_label_classes) else str(pred)
            else:
                pred_label = str(pred)
        elif isinstance(sor_label_classes, dict):
            pred_label = sor_label_classes.get(str(pred), str(pred))
        else:
            pred_label = str(pred)
    else:
        pred_label = str(pred)
    
    return {
        "prediction": pred_label,
        "confidence": round(confidence, 2)
    }

# Auto-load saat import
load_sor_models()