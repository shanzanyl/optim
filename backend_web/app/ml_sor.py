# backend_web/app/ml_sor.py
# Model Dashboard SOR — BiGRU Feature Extractor + Stacking Classifier (window_size=80, stride=40)
# Preprocessing: per-segment normalization via normalization.py (BUKAN scaler.pkl)

import importlib.util
import joblib
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Load normalization.py langsung dari path absolut (tidak perlu sys.path) ──
_NORM_PATH = BASE_DIR / "models" / "sor" / "normalization.py"
_norm_spec = importlib.util.spec_from_file_location("normalization", _NORM_PATH)
_norm_module = importlib.util.module_from_spec(_norm_spec)
_norm_spec.loader.exec_module(_norm_module)
apply_normalization = _norm_module.apply_normalization

# ── Path model: BiGRU Feature Extractor + Stacking Classifier ────────────────
SOR_MODEL_PATHS = [
    BASE_DIR / "models" / "sor" / "bigru_feature_extractor_model.keras",
    Path.cwd() / "models" / "sor" / "bigru_feature_extractor_model.keras",
]
SOR_STACKING_PATHS = [
    BASE_DIR / "models" / "sor" / "stacking_classifier.joblib",
    Path.cwd() / "models" / "sor" / "stacking_classifier.joblib",
]
SOR_LABEL_PATHS = [
    BASE_DIR / "models" / "sor" / "label_encoder.pkl",
    Path.cwd() / "models" / "sor" / "label_encoder.pkl",
]

sor_model             = None   # model BiGRU penuh (dipakai untuk membangun feature extractor)
sor_feature_extractor  = None   # sub-model: input -> output layer GRU (64-dim feature)
sor_stacking           = None   # StackingClassifier (RF + XGB -> LogisticRegression)
sor_le                 = None


def load_sor_models():
    global sor_model, sor_feature_extractor, sor_stacking, sor_le

    logger.info("=" * 50)
    logger.info("[ML_SOR] 🔄 Loading SOR BiGRU Feature Extractor + Stacking Classifier (window=80, stride=40)...")
    logger.info(f"[ML_SOR]   BASE_DIR = {BASE_DIR}")
    logger.info(f"[ML_SOR]   CWD      = {Path.cwd()}")

    # Load BiGRU feature extractor model
    for path in SOR_MODEL_PATHS:
        logger.info(f"[ML_SOR]   model path: {path} → exists={path.exists()}")
        if path.exists():
            try:
                import tensorflow as tf
                sor_model = tf.keras.models.load_model(str(path))
                logger.info(f"[ML_SOR] ✅ BiGRU model loaded: {path}")
                logger.info(f"[ML_SOR]   input_shape={sor_model.input_shape}, output_shape={sor_model.output_shape}")

                # Bangun ulang sub-model sampai layer GRU saja (nama layer: 'gru_4').
                # Dense terakhir di file model ini TIDAK dipakai — output-nya digantikan
                # oleh Stacking Classifier terpisah.
                inp = tf.keras.Input(shape=(80, 1))
                x = inp
                for layer in sor_model.layers:
                    x = layer(x)
                    if layer.name == "gru_4":
                        break
                sor_feature_extractor = tf.keras.Model(inputs=inp, outputs=x)
                logger.info(f"[ML_SOR] ✅ Feature extractor built: output_shape={sor_feature_extractor.output_shape}")
                break
            except Exception as e:
                logger.warning(f"[ML_SOR] Failed to load {path}: {e}")

    # Load Stacking Classifier
    for path in SOR_STACKING_PATHS:
        logger.info(f"[ML_SOR]   stacking path: {path} → exists={path.exists()}")
        if path.exists():
            try:
                sor_stacking = joblib.load(path)
                logger.info(f"[ML_SOR] ✅ Stacking classifier loaded: {path}")
                logger.info(f"[ML_SOR]   n_features_in_={getattr(sor_stacking, 'n_features_in_', '?')}")
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

    if sor_feature_extractor is None:
        logger.error("[ML_SOR] ❌ BiGRU feature extractor NOT loaded")
    if sor_stacking is None:
        logger.error("[ML_SOR] ❌ Stacking classifier NOT loaded")
    if sor_le is None:
        logger.error("[ML_SOR] ❌ Label encoder NOT loaded")


def predict_sor_batch(backscatter_data: list, window_size: int = 80, stride: int = 40) -> list:
    if sor_feature_extractor is None:
        raise Exception("[ML_SOR] Feature extractor is None — model belum dimuat")
    if sor_stacking is None:
        raise Exception("[ML_SOR] Stacking classifier is None — model belum dimuat")
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

    # ── Normalisasi per-segment (menggantikan StandardScaler) ─────────────────
    # Setiap baris (window) dinormalisasi secara independen:
    #   normalized = (window - mean) / (std + 1e-8)
    X_normalized = apply_normalization(X_all)  # shape: (total_windows, window_size)

    logger.info(f"[ML_SOR] ✅ Per-segment normalization applied")

    # Reshape untuk BiGRU: (batch, timesteps, features) = (total_windows, window_size, 1)
    X_input = X_normalized.reshape(total_windows, window_size, 1)

    logger.info(f"[ML_SOR] 🔄 Extracting {total_windows} window features via BiGRU...")

    # Tahap 1: ekstraksi fitur 64-dimensi lewat layer GRU
    features = sor_feature_extractor.predict(X_input, batch_size=256, verbose=0)
    # features shape: (total_windows, 64)

    logger.info(f"[ML_SOR] 🔄 Running Stacking Classifier on extracted features...")

    # Tahap 2: klasifikasi oleh Stacking Classifier
    proba_all = sor_stacking.predict_proba(features)
    # proba_all shape: (total_windows, n_classes)

    preds       = np.argmax(proba_all, axis=1)
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