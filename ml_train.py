#!/usr/bin/env python3
"""ML retraining script for Bunga Trader v2.

Reads data/ml_training/training_data.jsonl, filters labelled examples,
trains a logistic regression model to predict win/loss from features,
saves the model + feature weights.
"""

import json
import os
import pickle
import sys
from pathlib import Path

# ── Optional sklearn ──────────────────────────────────────────────
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# ── Paths (root-relative) ─────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "ml_training" / "training_data.jsonl"
MODEL_FILE = ROOT / "data" / "ml_training" / "model.pkl"
WEIGHTS_FILE = ROOT / "data" / "ml_training" / "feature_weights.json"
MIN_SAMPLES = 20

FEATURE_NAMES = [
    "atr",
    "regime_score",
    "mtf_alignment",
    "supertrend_dir",
    "stoch_rsi_k",
    "quality_score",
]


def regime_to_score(regime: str) -> float:
    return {"trending": 1.0, "ranging": 0.5}.get(regime, 0.0)


def load_data() -> tuple[list[list[float]], list[int]]:
    """Load labelled training data. Returns (X, y)."""
    if not DATA_FILE.exists():
        print(f"Training data file not found: {DATA_FILE}")
        sys.exit(0)

    X: list[list[float]] = []
    y: list[int] = []

    with open(DATA_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            outcome = rec.get("outcome")
            if outcome is None:
                continue

            # Features live under `features`; quality_score is at TOP LEVEL.
            feats = rec.get("features", {}) or {}
            # Skip rows with missing critical features
            atr_val = feats.get("atr")
            mtf_val = feats.get("mtf_alignment")
            st_val = feats.get("supertrend_dir")
            stoch_val = feats.get("stoch_rsi_k")
            qs_val = rec.get("quality_score") or feats.get("quality_score")
            # clsuter/regime are optional-ish but needed for the model
            regime_str = feats.get("regime", "unknown")

            if any(v is None for v in [atr_val, mtf_val, st_val, stoch_val, qs_val]):
                continue

            row = [
                float(atr_val),
                regime_to_score(regime_str),
                float(mtf_val),
                float(st_val),
                float(stoch_val),
                float(qs_val),
            ]
            X.append(row)
            y.append(1 if outcome == "win" else 0)

    return X, y


def train_sklearn(X, y):
    """Train logistic regression with sklearn."""
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)

    weights = dict(zip(FEATURE_NAMES, model.coef_[0].tolist()))

    print(f"Model:     Logistic Regression (sklearn)")
    print(f"Accuracy:  {acc:.4f}")
    print(f"Precision: {prec:.4f}")
    print(f"Recall:    {rec:.4f}")
    print(f"Features:  {len(FEATURE_NAMES)}")
    print(f"Train set: {len(X_train)}  Test set: {len(X_test)}")
    print(f"\nFeature weights (coefficients):")
    for name, coef in weights.items():
        print(f"  {name}: {coef:+.6f}")

    # Save model
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(model, f)
    print(f"\nModel saved to {MODEL_FILE}")

    return weights


def train_fallback(X, y):
    """Numpy-based fallback when sklearn is unavailable."""
    import numpy as np

    arr = np.array(X)
    labels = np.array(y)

    # Split manually (80/20)
    np.random.seed(42)
    idx = np.random.permutation(len(arr))
    split = int(0.8 * len(arr))
    train_idx, test_idx = idx[:split], idx[split:]

    X_train, X_test = arr[train_idx], arr[test_idx]
    y_train, y_test = labels[train_idx], labels[test_idx]

    # Simple approach: compute mean per-feature for wins vs losses
    wins = X_train[y_train == 1]
    losses = X_train[y_train == 0]

    if len(wins) == 0 or len(losses) == 0:
        print("Fallback: only one class present, using equal weights.")
        weights = {name: 0.0 for name in FEATURE_NAMES}
    else:
        win_means = wins.mean(axis=0)
        loss_means = losses.mean(axis=0)
        diffs = win_means - loss_means

        # Normalise to feature weights
        max_abs = np.max(np.abs(diffs)) if np.max(np.abs(diffs)) > 0 else 1.0
        weights = dict(zip(FEATURE_NAMES, (diffs / max_abs).tolist()))

    # Evaluate (compute direction-correctness as accuracy proxy)
    if len(wins) > 0 and len(losses) > 0:
        direction = np.sign(arr.mean(axis=0) * (diffs if len(wins) > 0 else 1))
        y_pred = np.where(np.dot(X_test, direction) > 0, 1, 0)
        acc = np.mean(y_pred == y_test)
    else:
        acc = 0.0

    print(f"Model:     Logistic Regression (numpy fallback)")
    print(f"Accuracy:  {acc:.4f}  (direction-based proxy)")
    print(f"# features: {len(FEATURE_NAMES)}")
    print(f"Train set: {len(X_train)}  Test set: {len(X_test)}")
    print(f"\nFeature weights (normalised mean diff):")
    for name, w in weights.items():
        print(f"  {name}: {w:+.6f}")

    return weights


def main():
    X, y = load_data()

    n_labelled = len(y)
    if n_labelled < MIN_SAMPLES:
        print(f"Not enough labelled data (need {MIN_SAMPLES}, have {n_labelled})")
        # Still save whatever we have as weights if there's any data
        if n_labelled > 0:
            n_wins = sum(y)
            n_losses = n_labelled - n_wins
            print(f"  Wins: {n_wins}  Losses: {n_losses}")
            # Save minimal placeholder weights so downstream can still read the file
            placeholder = {name: 0.0 for name in FEATURE_NAMES}
            with open(WEIGHTS_FILE, "w") as f:
                json.dump(placeholder, f, indent=2)
            print(f"Placeholder weights saved to {WEIGHTS_FILE}")
        sys.exit(0)

    n_wins = sum(y)
    n_losses = n_labelled - n_wins
    print(f"Loaded {n_labelled} labelled samples ({n_wins} wins, {n_losses} losses)")
    print(f"Features: {FEATURE_NAMES}")

    if SKLEARN_AVAILABLE:
        weights = train_sklearn(X, y)
    else:
        print("sklearn not available — using numpy fallback")
        weights = train_fallback(X, y)

    # Save feature weights
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=2)
    print(f"Feature weights saved to {WEIGHTS_FILE}")

    # Summary
    print(f"\n--- Retraining complete ---")
    print(f"Samples: {n_labelled} | Wins: {n_wins} | Losses: {n_losses}")
    print(f"Model:   {'sklearn (saved)' if SKLEARN_AVAILABLE else 'numpy fallback'}")


if __name__ == "__main__":
    main()
