# backend_web/app/ml_sor.py
# Model Random Forest untuk Dashboard SOR

import joblib
import json
import numpy as np
import pandas as pd
import warnings
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PATH - Model Random Forest untuk SOR
# ══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent

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

# Cache DataFrame columns — dibuat sekali saat load, dipakai di setiap predict
_df_columns = None


def load_sor_models():
    """Load semua model Random Forest untuk SOR"""
    global sor_model, sor_scaler, sor_feature_names, sor_label_classes, _df_columns

    logger.info("=" * 50)
    logger.info("[ML_SOR] 🔄 Loading SOR Random Forest models...")
    logger.info(f"[ML_SOR]   BASE_DIR = {BASE_DIR}")
    logger.info(f"[ML_SOR]   CWD      = {Path.cwd()}")

    for p in SOR_MODEL_PATHS:
        logger.info(f"[ML_SOR]   model path check: {p} → exists={p.exists()}")

    # Load model
    for path in SOR_MODEL_PATHS:
        if path.exists():
            try:
                sor_model = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ SOR model loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load scaler
    for path in SOR_SCALER_PATHS:
        if path.exists():
            try:
                sor_scaler = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ SOR scaler loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load feature names
    for path in SOR_FEATURE_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_feature_names = json.load(f)
                logger.info(f"[ML_SOR] ✅ feature names loaded: {path} ({len(sor_feature_names)} features)")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load label classes
    for path in SOR_LABEL_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_label_classes = json.load(f)
                logger.info(f"[ML_SOR] ✅ label classes loaded: {sor_label_classes}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Siapkan nama kolom DataFrame — pakai feature_names kalau ada,
    # fallback ke t000..t127 supaya scaler tidak komplain
    if sor_feature_names:
        _df_columns = sor_feature_names
    else:
        _df_columns = [f"t{i:03d}" for i in range(128)]

    logger.info(f"[ML_SOR]   _df_columns (sample): {_df_columns[:5]} ... {_df_columns[-3:]}")

    if sor_model is None:
        logger.warning("[ML_SOR] ⚠️ SOR model NOT FOUND!")
        logger.warning(f"[ML_SOR]   Paths tried: {[str(p) for p in SOR_MODEL_PATHS]}")
    else:
        logger.info(f"[ML_SOR] ✅ All artifacts ready. type={type(sor_model).__name__}")

    logger.info("=" * 50)
    return sor_model is not None


def predict_sor_window(window_data: list) -> dict:
    """
    Prediksi satu window dengan Random Forest.

    FIX UTAMA: kirim input sebagai pd.DataFrame dengan nama kolom
    yang sama seperti saat training → hilangkan UserWarning dari scaler.
    """
    if sor_model is None:
        raise Exception("[ML_SOR] sor_model is None — model belum dimuat")

    if len(window_data) != 128:
        raise ValueError(f"[ML_SOR] window_data panjangnya {len(window_data)}, harus 128")

    # Konversi ke numpy, cek NaN/Inf
    arr = np.array(window_data, dtype=np.float64)
    if np.any(np.isnan(arr)) or np.any(np.isinf(arr)):
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    # Bungkus sebagai DataFrame dengan nama kolom yang benar
    # → scaler tidak akan raise UserWarning lagi
    X = pd.DataFrame([arr], columns=_df_columns)

    # Normalisasi
    if sor_scaler is not None:
        # Suppress warning sepenuhnya — kita sudah handle dengan DataFrame
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            X_scaled = sor_scaler.transform(X)
    else:
        X_scaled = X.values

    # Prediksi
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pred = sor_model.predict(X_scaled)[0]

    # Confidence
    confidence = 95.0
    if hasattr(sor_model, 'predict_proba'):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            proba = sor_model.predict_proba(X_scaled)[0]
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