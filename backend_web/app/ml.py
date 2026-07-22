# backend_web/app/ml.py
# Model Random Forest untuk Detection — artefak model baru (.pkl, MinMaxScaler, 6 kelas)

import joblib
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PATH — Model RF untuk OTDR (folder models/otdr/)
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


OTDR_MODEL_PATHS   = _candidates("rf_model.pkl")
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

rf_model      = _load_first(OTDR_MODEL_PATHS,   "Random Forest model")
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

    if rf_model is not None and hasattr(rf_model, "n_features_in_"):
        if rf_model.n_features_in_ != n_feat:
            logger.error(
                f"[ML] ⚠️ TIDAK COCOK: model mengharapkan {rf_model.n_features_in_} "
                f"fitur, feature_order berisi {n_feat}"
            )
        else:
            logger.info(f"[ML] Model         : {rf_model.n_features_in_} fitur ✅")

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
        if rf_model is not None and hasattr(rf_model, "n_classes_"):
            if rf_model.n_classes_ != len(classes):
                logger.error(
                    f"[ML] ⚠️ TIDAK COCOK: model punya {rf_model.n_classes_} kelas, "
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
# PREDICT FUNCTION
# ══════════════════════════════════════════════════════════════════

def predict_from_otdr(otdr_values: dict) -> dict:
    """Prediksi jenis gangguan dari parameter tabel event OTDR."""

    if rf_model is None or feature_columns is None:
        raise RuntimeError("Model klasifikasi tidak tersedia.")

    try:
        row = pd.DataFrame([otdr_values])

        # NaN adalah representasi yang benar untuk Fiber Cut (identik dengan
        # training), dan MinMaxScaler meneruskan NaN apa adanya.
        for col in feature_columns:
            if col not in row.columns:
                row[col] = np.nan # Kolom yang tidak ada diisi NaN — bukan 0.0.

        # TIDAK menggunakan fillna(). NaN dipertahankan sampai ke RF,
        X = row[feature_columns].astype(np.float64)

        if scaler is not None:
            X = scaler.transform(X)

        # Ambil label dan confidence dari sumber yang sama (predict_proba)
        # agar keduanya tidak mungkin berbeda.
        if hasattr(rf_model, "predict_proba"):
            proba = rf_model.predict_proba(X)[0] # probability untuk setiap kelas
            pred_int = int(np.argmax(proba))       # ambil indeks kelas dengan probabilitas tertinggi
            confidence = float(proba[pred_int]) * 100
        else:
            pred_int = int(round(float(rf_model.predict(X)[0])))
            confidence = 0.0

        if label_encoder is not None:
            label = str(label_encoder.inverse_transform([pred_int])[0])
        else:
            labels = ["Air Gap", "Bad Splice", "Bending",
                      "Dirty Connector", "Fiber Cut", "Normal"]
            label = labels[pred_int % len(labels)]

        logger.info(f"🤖 Random Forest → {label} (confidence: {confidence:.1f}%)")

        return {
            "prediction": label,
            "confidence": round(confidence, 2),
            "status": get_status(label),
        }

    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise RuntimeError("Prediksi gagal. Periksa log untuk detail.") from e
