
import numpy as np

def normalize_per_segment(window):
    mean_segment, std_segment = window.mean(), window.std()
    # Add a small epsilon to avoid division by zero if std_segment is 0
    return (window - mean_segment) / (std_segment + 1e-8)

def apply_normalization(X):
    return np.array([normalize_per_segment(row) for row in X])
