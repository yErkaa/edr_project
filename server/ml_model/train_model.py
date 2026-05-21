"""
Обучение и сравнение 5 моделей обнаружения ПД казахстанских школьников.
Источники данных:
  1. datasets/synthetic_kz_students.csv  (синтетический v1)
  2. datasets/synthetic_kz_v2.csv        (сложные случаи v2)
  3. datasets/synthetic_kz_v3.csv        (максимально сложные v3)
  4. datasets/train.json                 (Kaggle PII, опциональный)

Выход:
  pii_model.pkl           — лучшая модель (по F1-weighted)
  vectorizer.pkl          — TF-IDF векторайзер
  comparison_table.csv    — метрики всех 5 моделей
  model_comparison.png    — столбчатая диаграмма метрик
  roc_curves.png          — ROC-кривые
  confusion_matrices.png  — матрицы ошибок
"""
import ast
import json
import os

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import (ExtraTreesClassifier,
                               GradientBoostingClassifier,
                               RandomForestClassifier)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report,
                              confusion_matrix, f1_score, precision_score,
                              recall_score, roc_auc_score, roc_curve)
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB

BASE_DIR    = os.path.dirname(__file__)
SYNTH_V1    = os.path.join(BASE_DIR, "datasets", "synthetic_kz_students.csv")
SYNTH_V2    = os.path.join(BASE_DIR, "datasets", "synthetic_kz_v2.csv")
SYNTH_V3    = os.path.join(BASE_DIR, "datasets", "synthetic_kz_v3.csv")
KAGGLE_JSON = os.path.join(BASE_DIR, "datasets", "train.json")
KAGGLE_CSV  = os.path.join(BASE_DIR, "datasets", "pii_detection_train.csv")
MODEL_PATH  = os.path.join(BASE_DIR, "pii_model.pkl")
VEC_PATH    = os.path.join(BASE_DIR, "vectorizer.pkl")
TABLE_CSV   = os.path.join(BASE_DIR, "comparison_table.csv")
CHART_BAR   = os.path.join(BASE_DIR, "model_comparison.png")
CHART_ROC   = os.path.join(BASE_DIR, "roc_curves.png")
CHART_CM    = os.path.join(BASE_DIR, "confusion_matrices.png")

METRICS  = ["Accuracy", "Precision", "Recall", "F1-weighted", "F1-macro", "ROC-AUC"]
PALETTE  = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2"]
M_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2", "#937860"]


# ── Загрузка данных ───────────────────────────────────────────────────────────

def _load_csv(path: str, tag: str) -> "pd.DataFrame | None":
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)[["text", "label"]].dropna()
    print(f"[INFO] {tag}: {len(df)} записей")
    return df


def load_all() -> pd.DataFrame:
    parts = []
    for path, tag in [
        (SYNTH_V1, "v1 базовый"),
        (SYNTH_V2, "v2 сложный"),
        (SYNTH_V3, "v3 максимально сложный"),
    ]:
        df = _load_csv(path, tag)
        if df is not None:
            parts.append(df)

    if not parts:
        raise FileNotFoundError(
            "Ни один датасет не найден. Запустите generate_dataset*.py"
        )

    if os.path.exists(KAGGLE_JSON):
        print(f"[INFO] Kaggle JSON: загружаем...")
        with open(KAGGLE_JSON, encoding="utf-8") as f:
            docs = json.load(f)
        rows = []
        for doc in docs:
            text    = doc.get("full_text") or " ".join(doc.get("tokens", []))
            labels  = doc.get("labels", [])
            has_pii = int(any(lbl != "O" for lbl in labels))
            if text:
                rows.append({"text": text, "label": has_pii})
        kdf = pd.DataFrame(rows)
        print(f"[INFO] Kaggle JSON: {len(kdf)} документов")
        parts.append(kdf)
    elif os.path.exists(KAGGLE_CSV):
        df = pd.read_csv(KAGGLE_CSV, nrows=50_000)
        if "text" in df.columns and "label" in df.columns:
            parts.append(df[["text", "label"]].dropna())

    result = pd.concat(parts, ignore_index=True)
    n_pii = result["label"].sum()
    print(f"[INFO] Итого: {len(result)} записей (с ПД: {n_pii}, без ПД: {len(result)-n_pii})")
    return result


# ── Метрики ───────────────────────────────────────────────────────────────────

def compute_metrics(model, X_val, y_val) -> dict:
    preds = model.predict(X_val)
    proba = (model.predict_proba(X_val)[:, 1]
             if hasattr(model, "predict_proba")
             else model.decision_function(X_val))
    return {
        "Accuracy":    round(accuracy_score(y_val, preds), 4),
        "Precision":   round(precision_score(y_val, preds, average="weighted", zero_division=0), 4),
        "Recall":      round(recall_score(y_val, preds, average="weighted", zero_division=0), 4),
        "F1-weighted": round(f1_score(y_val, preds, average="weighted", zero_division=0), 4),
        "F1-macro":    round(f1_score(y_val, preds, average="macro", zero_division=0), 4),
        "ROC-AUC":     round(roc_auc_score(y_val, proba), 4),
        "_preds": preds,
        "_proba": proba,
    }


# ── Таблица в консоль ─────────────────────────────────────────────────────────

def print_table(results: dict) -> None:
    col = 14
    hdr = f"{'Модель':<22}" + "".join(f"{m:>{col}}" for m in METRICS)
    sep = "=" * len(hdr)
    print(f"\n{sep}\nСРАВНИТЕЛЬНАЯ ТАБЛИЦА МЕТРИК\n{sep}")
    print(hdr)
    print("-" * len(hdr))
    for name, m in results.items():
        row = f"{name:<22}" + "".join(f"{m[k]:>{col}.4f}" for k in METRICS)
        print(row)
    print(sep)


def save_csv(results: dict) -> None:
    rows = [{"Модель": n, **{k: m[k] for k in METRICS}}
            for n, m in results.items()]
    pd.DataFrame(rows).to_csv(TABLE_CSV, index=False, encoding="utf-8-sig")
    print(f"[OK] Таблица CSV: {TABLE_CSV}")


# ── График 1: столбчатая диаграмма метрик ────────────────────────────────────

def plot_bar(results: dict) -> None:
    names     = list(results.keys())
    n_m       = len(METRICS)
    x         = np.arange(len(names))
    width     = 0.13
    offsets   = np.linspace(-(n_m - 1) / 2 * width, (n_m - 1) / 2 * width, n_m)

    fig, ax = plt.subplots(figsize=(14, 6))
    for i, (metric, color, offset) in enumerate(zip(METRICS, M_COLORS, offsets)):
        vals = [results[n][metric] for n in names]
        bars = ax.bar(x + offset, vals, width, label=metric,
                      color=color, alpha=0.85, edgecolor="white")
        for bar, val in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.004,
                f"{val:.3f}",
                ha="center", va="bottom", fontsize=6, rotation=90,
            )

    ax.set_xlabel("Модель", fontsize=12)
    ax.set_ylabel("Значение метрики", fontsize=12)
    ax.set_title("Сравнение 5 моделей по ключевым метрикам", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylim(0.6, 1.12)
    ax.legend(loc="lower right", fontsize=9, ncol=2)
    ax.yaxis.grid(True, alpha=0.3)
    ax.set_axisbelow(True)
    plt.tight_layout()
    plt.savefig(CHART_BAR, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[OK] График: {CHART_BAR}")


# ── График 2: ROC-кривые ──────────────────────────────────────────────────────

def plot_roc(results: dict, y_val) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    for (name, m), color in zip(results.items(), PALETTE):
        fpr, tpr, _ = roc_curve(y_val, m["_proba"])
        ax.plot(fpr, tpr, color=color, lw=2,
                label=f"{name}  (AUC = {m['ROC-AUC']:.4f})")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.4, label="Random")
    ax.set_xlabel("False Positive Rate", fontsize=12)
    ax.set_ylabel("True Positive Rate", fontsize=12)
    ax.set_title("ROC-кривые всех моделей", fontsize=14, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHART_ROC, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[OK] График: {CHART_ROC}")


# ── График 3: матрицы ошибок ──────────────────────────────────────────────────

def plot_cm(results: dict, y_val) -> None:
    names = list(results.keys())
    n     = len(names)
    cols  = 3
    rows  = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(13, 4.5 * rows))
    axes = np.array(axes).flatten()

    for idx, (name, m) in enumerate(results.items()):
        cm = confusion_matrix(y_val, m["_preds"])
        ax = axes[idx]
        im = ax.imshow(cm, interpolation="nearest", cmap="Blues")
        ax.set_title(name, fontsize=11, fontweight="bold")
        ax.set_xlabel("Предсказано", fontsize=9)
        ax.set_ylabel("Истинно", fontsize=9)
        ax.set_xticks([0, 1])
        ax.set_yticks([0, 1])
        ax.set_xticklabels(["Без ПД", "С ПД"], fontsize=9)
        ax.set_yticklabels(["Без ПД", "С ПД"], fontsize=9)
        thresh = cm.max() / 2
        for i in range(2):
            for j in range(2):
                ax.text(j, i, f"{cm[i, j]:,}", ha="center", va="center",
                        fontsize=13, fontweight="bold",
                        color="white" if cm[i, j] > thresh else "black")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    for idx in range(n, len(axes)):
        axes[idx].set_visible(False)

    fig.suptitle("Матрицы ошибок (Confusion Matrices)",
                 fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(CHART_CM, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[OK] График: {CHART_CM}")


# ── Обучение ──────────────────────────────────────────────────────────────────

def main():
    df = load_all()
    X  = df["text"].astype(str)
    y  = df["label"].astype(int)

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"[INFO] Train: {len(X_train)}  Val: {len(X_val)}")

    print("[INFO] Векторизация TF-IDF...")
    vectorizer = TfidfVectorizer(
        max_features=10_000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=2,
    )
    X_train_v = vectorizer.fit_transform(X_train)
    X_val_v   = vectorizer.transform(X_val)

    candidates = {
        "MultinomialNB":      MultinomialNB(alpha=0.5),
        "LogisticRegression": LogisticRegression(max_iter=1000, C=0.5, random_state=42),
        "ExtraTrees":         ExtraTreesClassifier(n_estimators=200, max_depth=20,
                                                   random_state=42, n_jobs=-1),
        "RandomForest":       RandomForestClassifier(n_estimators=200, max_depth=20,
                                                     random_state=42, n_jobs=-1),
        "GradientBoosting":   GradientBoostingClassifier(n_estimators=100, max_depth=5,
                                                          learning_rate=0.1, random_state=42),
    }

    results    = {}
    best_name  = None
    best_model = None
    best_f1    = -1.0

    for name, model in candidates.items():
        print(f"[INFO] Обучаем {name}...", end=" ", flush=True)
        model.fit(X_train_v, y_train)
        m  = compute_metrics(model, X_val_v, y_val)
        results[name] = m
        f1   = m["F1-weighted"]
        mark = " << лучший" if f1 > best_f1 else ""
        print(f"F1={f1:.4f}  AUC={m['ROC-AUC']:.4f}{mark}")
        if f1 > best_f1:
            best_f1, best_name, best_model = f1, name, model

    print_table(results)

    print(f"\nClassification report — {best_name}:")
    print(classification_report(
        y_val, results[best_name]["_preds"],
        target_names=["Без ПД", "С ПД"]
    ))

    joblib.dump(best_model, MODEL_PATH)
    joblib.dump(vectorizer, VEC_PATH)
    print(f"[OK] Модель ({best_name}): {MODEL_PATH}")
    print(f"[OK] Векторайзер:          {VEC_PATH}")

    save_csv(results)
    plot_bar(results)
    plot_roc(results, y_val)
    plot_cm(results, y_val)

    print(f"\n{'='*55}")
    print(f"Лучшая модель: {best_name}  (F1={best_f1:.4f})")
    print("=" * 55)


if __name__ == "__main__":
    main()
