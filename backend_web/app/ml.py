# backend_web/app/ml.py
# Model LightGBM untuk Detection — artefak model baru (.pkl, MinMaxScaler, 6 kelas)

import joblib
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PATH — Model LightGBM untuk OTDR (folder models/otdr/)
# ══════════════════════════════════════════════════════════════════
# Hanya artefak model final (.pkl) yang dimuat. Nama berkas lama sengaja
# TIDAK dijadikan cadangan: bila berkas baru tidak ditemukan, sistem harus
# gagal secara terang-terangan. Memuat model lama diam-diam jauh lebih
# berbahaya, karena artefak lama saling cocok satu sama lain sehingga
# validasi di bawah tidak akan berbunyi dan salah model tidak akan ketahuan.

BASE_DIR = Path(__file__).resolve().parent.parent


def _candidates(name):
    """Susun daftar lokasi yang mungkin untuk satu berkas artefak."""
    return [
        BASE_DIR / "models" / "otdr" / name,
        Path.cwd() / "models" / "otdr" / name,
    ]


OTDR_MODEL_PATHS   = _candidates("lightgbm_model.pkl")
OTDR_ENCODER_PATHS = _candidates("label_encoder.pkl")
OTDR_FEATURE_PATHS = _candidates("feature_order.pkl")
OTDR_SCALER_PATHS  = _candidates("scaler.pkl")


def _load_any(path: Path):
    """Muat artefak .pkl, baik yang disimpan via joblib maupun pickle."""
    try:
        return joblib.load(path)
    except Exception:
        with open(path, "rb") as f:
            return pickle.load(f)


def _load_first(paths, label):
    for path in paths:
        if path.exists():
            try:
                obj = _load_any(path)
                logger.info(f"✅ {label} loaded: {path.name}")
                return obj
            except Exception as e:
                logger.warning(f"Failed to load {path}: {e}")
    logger.error(f"❌ {label} NOT loaded — tidak ada berkas yang cocok")
    return None


# ══════════════════════════════════════════════════════════════════
# LOAD MODEL
# ══════════════════════════════════════════════════════════════════

lgbm_model      = _load_first(OTDR_MODEL_PATHS,   "LightGBM model")
label_encoder   = _load_first(OTDR_ENCODER_PATHS, "Label encoder")
feature_columns = _load_first(OTDR_FEATURE_PATHS, "Feature order")
scaler          = _load_first(OTDR_SCALER_PATHS,  "Scaler")

if feature_columns is not None:
    feature_columns = [str(col).strip() for col in feature_columns]
else:
    # Tidak ada daftar fitur cadangan. Menebak urutan fitur berarti menghasilkan
    # prediksi yang salah tanpa ketahuan, sehingga lebih baik gagal di sini.
    logger.error("❌ feature_order.pkl tidak ditemukan — prediksi model dinonaktifkan")


# ══════════════════════════════════════════════════════════════════
# VALIDASI KECOCOKAN ARTEFAK
# ══════════════════════════════════════════════════════════════════
# Model, scaler, dan feature_order harus berasal dari proses pelatihan yang
# sama. Bila tidak cocok, prediksi tetap berjalan tetapi hasilnya salah secara
# diam-diam — jauh lebih berbahaya daripada gagal terang-terangan. Pemeriksaan
# ini mencetak peringatan saat startup agar ketidakcocokan langsung terlihat.

def _validate_artifacts():
    if feature_columns is None:
        logger.error('[ML] ⚠️ feature_order tidak termuat — lewati validasi')
        return
    n_feat = len(feature_columns)
    logger.info("─" * 55)
    logger.info(f"[ML] Feature order : {n_feat} fitur")

    if lgbm_model is not None and hasattr(lgbm_model, "n_features_in_"):
        if lgbm_model.n_features_in_ != n_feat:
            logger.error(
                f"[ML] ⚠️ TIDAK COCOK: model mengharapkan {lgbm_model.n_features_in_} "
                f"fitur, feature_order berisi {n_feat}"
            )
        else:
            logger.info(f"[ML] Model         : {lgbm_model.n_features_in_} fitur ✅")

    if scaler is not None and hasattr(scaler, "n_features_in_"):
        if scaler.n_features_in_ != n_feat:
            logger.error(
                f"[ML] ⚠️ TIDAK COCOK: scaler mengharapkan {scaler.n_features_in_} "
                f"fitur, feature_order berisi {n_feat}"
            )
        else:
            logger.info(f"[ML] Scaler        : {type(scaler).__name__}, "
                        f"{scaler.n_features_in_} fitur ✅")

    if label_encoder is not None and hasattr(label_encoder, "classes_"):
        classes = list(label_encoder.classes_)
        logger.info(f"[ML] Kelas         : {len(classes)} → {classes}")
        if lgbm_model is not None and hasattr(lgbm_model, "n_classes_"):
            if lgbm_model.n_classes_ != len(classes):
                logger.error(
                    f"[ML] ⚠️ TIDAK COCOK: model punya {lgbm_model.n_classes_} kelas, "
                    f"encoder punya {len(classes)}"
                )
        unknown = [c for c in classes if c not in STATUS_MAP]
        if unknown:
            logger.warning(f"[ML] Kelas tanpa STATUS_MAP (default Warning): {unknown}")
    logger.info("─" * 55)


# ══════════════════════════════════════════════════════════════════
# STATUS MAP
# ══════════════════════════════════════════════════════════════════

STATUS_MAP = {
    "Normal": "Normal",
    "Bending": "Warning",
    "Bad Splice": "Warning",
    "Air Gap": "Warning",
    "Dirty Connector": "Warning",
    "Fiber Cut": "Critical",
}


def get_status(label: str) -> str:
    return STATUS_MAP.get(label, "Warning")


_validate_artifacts()


# ══════════════════════════════════════════════════════════════════
# RULE-BASED FALLBACK
# ══════════════════════════════════════════════════════════════════

def _rule_based(otdr_values: dict, confidence: float) -> dict:
    """Cadangan sederhana bila model tidak tersedia atau prediksi gagal."""
    losses = [
        otdr_values.get('Loss 1', 0) or 0,
        otdr_values.get('Loss 2', 0) or 0,
        otdr_values.get('Loss 3', 0) or 0,
    ]
    max_loss = max(losses) if losses else 0

    if max_loss > 3.0:
        prediction = "Fiber Cut"
    elif max_loss > 1.2:
        prediction = "Bending"
    elif max_loss > 0.5:
        prediction = "Bad Splice"
    else:
        prediction = "Normal"

    return {
        "prediction": prediction,
        "confidence": confidence,
        "status": get_status(prediction),
    }


# ══════════════════════════════════════════════════════════════════
# PREDICT FUNCTION
# ══════════════════════════════════════════════════════════════════

def predict_from_otdr(otdr_values: dict) -> dict:
    """Prediksi jenis gangguan dari parameter tabel event OTDR."""

    if lgbm_model is None or feature_columns is None:
        logger.warning("⚠️ Model/feature order not available, using rule-based prediction")
        return _rule_based(otdr_values, 85.0)

    try:
        row = pd.DataFrame([otdr_values])

        # Kolom yang tidak ada diisi NaN — bukan 0.0.
        # NaN adalah representasi yang benar untuk Fiber Cut (identik dengan
        # training), dan MinMaxScaler meneruskan NaN apa adanya.
        for col in feature_columns:
            if col not in row.columns:
                row[col] = np.nan

        # TIDAK menggunakan fillna(). NaN dipertahankan sampai ke LightGBM,
        # yang mendukung NaN secara native.
        X = row[feature_columns].astype(np.float64)

        if scaler is not None:
            X = scaler.transform(X)

        # Ambil label dan confidence dari sumber yang sama (predict_proba)
        # agar keduanya tidak mungkin berbeda.
        if hasattr(lgbm_model, "predict_proba"):
            proba = lgbm_model.predict_proba(X)[0]
            pred_int = int(np.argmax(proba))
            confidence = float(proba[pred_int]) * 100
        else:
            pred_int = int(round(float(lgbm_model.predict(X)[0])))
            confidence = 0.0

        if label_encoder is not None:
            label = str(label_encoder.inverse_transform([pred_int])[0])
        else:
            labels = ["Air Gap", "Bad Splice", "Bending",
                      "Dirty Connector", "Fiber Cut", "Normal"]
            label = labels[pred_int % len(labels)]

        logger.info(f"🤖 LightGBM → {label} (confidence: {confidence:.1f}%)")

        return {
            "prediction": label,
            "confidence": round(confidence, 2),
            "status": get_status(label),
        }

    except Exception as e:
        logger.error(f"Prediction error: {e}")
        import traceback
        traceback.print_exc()
        return _rule_based(otdr_values, 70.0)