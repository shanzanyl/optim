# backend_web/app/ml_sor.py
# Model LSTM untuk Dashboard SOR — window_size=50, stride=25

import joblib
import numpy as np
import warnings
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Path model LSTM ──────────────────────────────────────────
SOR_MODEL_PATHS = [
    BASE_DIR / "models" / "sor" / "lstm_model_50_25.keras",
    Path.cwd() / "models" / "sor" / "lstm_model_50_25.keras",
]
SOR_SCALER_PATHS = [
    BASE_DIR / "models" / "sor" / "standard_scaler_50_25.joblib",
    Path.cwd() / "models" / "sor" / "standard_scaler_50_25.joblib",
]
SOR_LABEL_PATHS = [
    BASE_DIR / "models" / "sor" / "label_encoder_50_25.joblib",
    Path.cwd() / "models" / "sor" / "label_encoder_50_25.joblib",
]

sor_model  = None
sor_scaler = None
sor_le     = None


def load_sor_models():
    global sor_model, sor_scaler, sor_le

    logger.info("=" * 50)
    logger.info("[ML_SOR] 🔄 Loading SOR LSTM models (window=50, stride=25)...")
    logger.info(f"[ML_SOR]   BASE_DIR = {BASE_DIR}")
    logger.info(f"[ML_SOR]   CWD      = {Path.cwd()}")

    # Load LSTM model
    for path in SOR_MODEL_PATHS:
        logger.info(f"[ML_SOR]   model path: {path} → exists={path.exists()}")
        if path.exists():
            try:
                import tensorflow as tf
                sor_model = tf.keras.models.load_model(str(path))
                logger.info(f"[ML_SOR] ✅ LSTM model loaded: {path}")
                logger.info(f"[ML_SOR]   input_shape={sor_model.input_shape}, output_shape={sor_model.output_shape}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load scaler
    for path in SOR_SCALER_PATHS:
        if path.exists():
            try:
                sor_scaler = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ Scaler loaded: {path}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load label encoder
    for path in SOR_LABEL_PATHS:
        if path.exists():
            try:
                sor_le = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ Label encoder loaded: {path}")
                logger.info(f"[ML_SOR]   classes={sor_le.classes_.tolist()}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    if sor_model is None:
        logger.error("[ML_SOR] ❌ LSTM model NOT loaded")
    if sor_scaler is None:
        logger.error("[ML_SOR] ❌ Scaler NOT loaded")
    if sor_le is None:
        logger.error("[ML_SOR] ❌ Label encoder NOT loaded")


def predict_sor_batch(backscatter_data: list, window_size: int = 50, stride: int = 25) -> list:
    """
    BATCH PREDICT dengan LSTM — window_size=50, stride=25.

    Pipeline:
    1. Sliding window pada data backscatter (kolom Loss dB)
    2. Setiap window di-scale dengan StandardScaler
    3. Reshape ke (batch, window_size, 1) untuk LSTM
    4. Prediksi batch, decode label

    Args:
        backscatter_data: list nilai backscatter/loss dari CSV
        window_size: ukuran sliding window (default 50)
        stride: pergeseran antar window (default 25)

    Returns:
        list of dict: [{start, end, prediction, confidence}, ...]
    """
    if sor_model is None:
        raise Exception("[ML_SOR] LSTM model is None — model belum dimuat")
    if sor_scaler is None:
        raise Exception("[ML_SOR] Scaler is None — scaler belum dimuat")
    if sor_le is None:
        raise Exception("[ML_SOR] Label encoder is None — label encoder belum dimuat")

    n = len(backscatter_data)
    total_windows = max(0, (n - window_size) // stride + 1)

    if total_windows <= 0:
        raise ValueError(
            f"[ML_SOR] Data hanya {n} titik, tidak cukup untuk window_size={window_size}"
        )

    logger.info(f"[ML_SOR] 🔄 Building matrix {total_windows} × {window_size} (stride={stride})...")

    arr = np.array(backscatter_data, dtype=np.float64)

    # Bersihkan NaN/Inf
    if np.any(np.isnan(arr)) or np.any(np.isinf(arr)):
        logger.warning("[ML_SOR] ⚠️ Data mengandung NaN/Inf, dibersihkan dengan 0")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    # Vectorized sliding window — shape: (total_windows, window_size)
    shape   = (total_windows, window_size)
    strides = (arr.strides[0] * stride, arr.strides[0])
    X_all   = np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides).copy()

    logger.info(f"[ML_SOR] ✅ Matrix built: shape={X_all.shape}")

    # Scale: StandardScaler (fit per window — transform seluruh batch)
    # StandardScaler dilatih dengan shape (n_samples, window_size)
    X_scaled = sor_scaler.transform(X_all)  # shape: (total_windows, window_size)

    # Reshape untuk LSTM: (batch, timesteps, features) = (total_windows, window_size, 1)
    X_lstm = X_scaled.reshape(total_windows, window_size, 1)

    logger.info(f"[ML_SOR] 🔄 Running LSTM batch predict on {total_windows} windows...")

    # Predict semua window sekaligus
    proba_all = sor_model.predict(X_lstm, batch_size=256, verbose=0)
    # proba_all shape: (total_windows, n_classes)

    preds = np.argmax(proba_all, axis=1)
    confidences = np.max(proba_all, axis=1)

    # Decode label
    labels = sor_le.inverse_transform(preds)

    # Susun hasil
    results = []
    for i in range(total_windows):
        start = i * stride
        end   = start + window_size - 1
        results.append({
            "start"     : int(start),
            "end"       : int(end),
            "prediction": str(labels[i]),
            "confidence": round(float(confidences[i]) * 100, 2),
        })

    logger.info(f"[ML_SOR] ✅ Done: {total_windows} windows predicted")
    return results