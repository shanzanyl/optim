# backend_web/app/ml_sor.py
# Model Random Forest untuk Dashboard SOR — BATCH PREDICT

import joblib
import json
import numpy as np
import pandas as pd
import warnings
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

sor_model = None
sor_scaler = None
sor_feature_names = None
sor_label_classes = None
_df_columns = None


def load_sor_models():
    global sor_model, sor_scaler, sor_feature_names, sor_label_classes, _df_columns

    logger.info("=" * 50)
    logger.info("[ML_SOR] 🔄 Loading SOR Random Forest models...")
    logger.info(f"[ML_SOR]   BASE_DIR = {BASE_DIR}")
    logger.info(f"[ML_SOR]   CWD      = {Path.cwd()}")

    for p in SOR_MODEL_PATHS:
        logger.info(f"[ML_SOR]   model path check: {p} → exists={p.exists()}")

    for path in SOR_MODEL_PATHS:
        if path.exists():
            try:
                sor_model = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ SOR model loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    for path in SOR_SCALER_PATHS:
        if path.exists():
            try:
                sor_scaler = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ SOR scaler loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    for path in SOR_FEATURE_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_feature_names = json.load(f)
                logger.info(f"[ML_SOR] ✅ feature names loaded ({len(sor_feature_names)} features)")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    for path in SOR_LABEL_PATHS:
        if path.exists():
            try:
                with open(path, 'r') as f:
                    sor_label_classes = json.load(f)
                logger.info(f"[ML_SOR] ✅ label classes: {sor_label_classes}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    _df_columns = sor_feature_names if sor_feature_names else [f"t{i:03d}" for i in range(128)]
    logger.info(f"[ML_SOR]   columns sample: {_df_columns[:3]} ... {_df_columns[-3:]}")

    if sor_model is None:
        logger.warning("[ML_SOR] ⚠️ SOR model NOT FOUND!")
    else:
        logger.info(f"[ML_SOR] ✅ All artifacts ready. type={type(sor_model).__name__}")
    logger.info("=" * 50)
    return sor_model is not None


def predict_sor_batch(backscatter_data: list, window_size: int = 128) -> list:
    """
    BATCH PREDICT — semua window diprediksi sekaligus dalam satu .predict() call.
    
    Jauh lebih cepat dari loop per-window:
    - Sebelumnya: 7738 × model.predict() = ~10 menit
    - Sekarang:   1 × model.predict(7738 rows) = ~5-15 detik

    Args:
        backscatter_data: list nilai Backscatter (dB) dari CSV
        window_size: ukuran sliding window (default 128)

    Returns:
        list of dict: [{start, end, prediction, confidence}, ...]
    """
    if sor_model is None:
        raise Exception("[ML_SOR] sor_model is None — model belum dimuat")

    n = len(backscatter_data)
    total_windows = n - window_size + 1

    if total_windows <= 0:
        raise ValueError(f"[ML_SOR] Data hanya {n} titik, tidak cukup untuk window size {window_size}")

    logger.info(f"[ML_SOR] 🔄 Building matrix {total_windows} × {window_size}...")

    # Bangun matrix semua window sekaligus — shape: (total_windows, 128)
    arr = np.array(backscatter_data, dtype=np.float64)
    
    # Vectorized sliding window menggunakan stride tricks (zero-copy, sangat cepat)
    shape = (total_windows, window_size)
    strides = (arr.strides[0], arr.strides[0])
    X_all = np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides).copy()

    # Cek dan bersihkan NaN/Inf
    if np.any(np.isnan(X_all)) or np.any(np.isinf(X_all)):
        logger.warning("[ML_SOR] ⚠️ Data mengandung NaN/Inf, dibersihkan dengan 0")
        X_all = np.nan_to_num(X_all, nan=0.0, posinf=0.0, neginf=0.0)

    logger.info(f"[ML_SOR] ✅ Matrix built: shape={X_all.shape}")

    # Bungkus sebagai DataFrame agar scaler tidak warning
    X_df = pd.DataFrame(X_all, columns=_df_columns)

    # Normalisasi batch
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if sor_scaler is not None:
            X_scaled = sor_scaler.transform(X_df)
        else:
            X_scaled = X_df.values

    logger.info(f"[ML_SOR] 🔄 Running batch predict on {total_windows} windows...")

    # Prediksi SEMUA window sekaligus — ini yang menghemat 99% waktu
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        preds = sor_model.predict(X_scaled)

        confidences = None
        if hasattr(sor_model, 'predict_proba'):
            probas = sor_model.predict_proba(X_scaled)
            confidences = np.max(probas, axis=1) * 100

    logger.info(f"[ML_SOR] ✅ Batch predict selesai: {len(preds)} prediksi")

    # Decode label
    def decode(pred):
        if sor_label_classes is None:
            return str(pred)
        if isinstance(sor_label_classes, list):
            if isinstance(pred, (int, np.integer)):
                return sor_label_classes[pred] if pred < len(sor_label_classes) else str(pred)
            return str(pred)
        if isinstance(sor_label_classes, dict):
            return sor_label_classes.get(str(pred), str(pred))
        return str(pred)

    # Susun hasil
    results = []
    for i, pred in enumerate(preds):
        conf = float(confidences[i]) if confidences is not None else 95.0
        results.append({
            "start": i,
            "end": i + window_size - 1,
            "prediction": decode(pred),
            "confidence": round(conf, 2),
        })

    return results


# Fungsi lama dipertahankan untuk kompatibilitas (tidak dipakai di endpoint baru)
def predict_sor_window(window_data: list) -> dict:
    if sor_model is None:
        raise Exception("[ML_SOR] sor_model is None")
    if len(window_data) != 128:
        raise ValueError(f"window_data panjangnya {len(window_data)}, harus 128")
    arr = np.array(window_data, dtype=np.float64)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    X = pd.DataFrame([arr], columns=_df_columns)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        X_scaled = sor_scaler.transform(X) if sor_scaler else X.values
        pred = sor_model.predict(X_scaled)[0]
        confidence = 95.0
        if hasattr(sor_model, 'predict_proba'):
            proba = sor_model.predict_proba(X_scaled)[0]
            confidence = float(np.max(proba) * 100)

    def decode(p):
        if sor_label_classes is None: return str(p)
        if isinstance(sor_label_classes, list):
            return sor_label_classes[p] if isinstance(p, (int, np.integer)) and p < len(sor_label_classes) else str(p)
        if isinstance(sor_label_classes, dict): return sor_label_classes.get(str(p), str(p))
        return str(p)

    return {"prediction": decode(pred), "confidence": round(confidence, 2)}


load_sor_models()