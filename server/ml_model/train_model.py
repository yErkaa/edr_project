"""
Обучение модели обнаружения ПД казахстанских школьников.
Источники данных:
  1. datasets/synthetic_kz_students.csv  (синтетический, обязательный)
  2. datasets/train.json                 (Kaggle PII competition, опциональный)
  3. datasets/pii_detection_train.csv    (Kaggle CSV, опциональный)

Обучает три модели, выбирает лучшую по F1, сохраняет pii_model.pkl + vectorizer.pkl.
"""
import ast
import json
import os

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split

BASE_DIR        = os.path.dirname(__file__)
SYNTH_PATH      = os.path.join(BASE_DIR, "datasets", "synthetic_kz_students.csv")
SYNTH_V2_PATH   = os.path.join(BASE_DIR, "datasets", "synthetic_kz_v2.csv")
KAGGLE_JSON     = os.path.join(BASE_DIR, "datasets", "train.json")
KAGGLE_CSV      = os.path.join(BASE_DIR, "datasets", "pii_detection_train.csv")
MODEL_PATH      = os.path.join(BASE_DIR, "pii_model.pkl")
VEC_PATH        = os.path.join(BASE_DIR, "vectorizer.pkl")


# ── Загрузка данных ───────────────────────────────────────────────────────────

def load_synthetic() -> pd.DataFrame:
    if not os.path.exists(SYNTH_PATH):
        raise FileNotFoundError(
            f"Синтетический датасет не найден: {SYNTH_PATH}\n"
            "Сначала запустите: python generate_dataset.py"
        )
    df = pd.read_csv(SYNTH_PATH)
    print(f"[INFO] Датасет v1 (базовый): {len(df)} записей")

    # Подгружаем v2 (сложные случаи) если он сгенерирован
    if os.path.exists(SYNTH_V2_PATH):
        df2 = pd.read_csv(SYNTH_V2_PATH)
        print(f"[INFO] Датасет v2 (сложные случаи): {len(df2)} записей")
        df = pd.concat([df, df2], ignore_index=True)
        print(f"[INFO] Итого синтетических: {len(df)} записей")
    else:
        print("[INFO] Датасет v2 не найден, пропускаем. "
              "Запустите: python generate_dataset_v2.py")

    return df[["text", "label"]].dropna()


def load_kaggle() -> pd.DataFrame | None:
    # ── Формат Kaggle PII competition: train.json ─────────────────────────────
    if os.path.exists(KAGGLE_JSON):
        print(f"[INFO] Загружаем Kaggle JSON: {KAGGLE_JSON}")
        with open(KAGGLE_JSON, encoding="utf-8") as f:
            docs = json.load(f)

        rows = []
        for doc in docs:
            text = doc.get("full_text") or " ".join(doc.get("tokens", []))
            labels = doc.get("labels", [])
            has_pii = int(any(lbl != "O" for lbl in labels))
            if text:
                rows.append({"text": text, "label": has_pii})

        df = pd.DataFrame(rows)
        pii_n    = df["label"].sum()
        no_pii_n = len(df) - pii_n
        print(f"[INFO] Kaggle JSON: {len(df)} документов  "
              f"(с ПД: {pii_n}, без ПД: {no_pii_n})")
        return df

    # ── Запасной вариант: CSV ─────────────────────────────────────────────────
    if os.path.exists(KAGGLE_CSV):
        print(f"[INFO] Загружаем Kaggle CSV: {KAGGLE_CSV}")
        df = pd.read_csv(KAGGLE_CSV, nrows=50_000)

        if "full_text" in df.columns and "labels" in df.columns:
            def _has_pii(val) -> int:
                try:
                    tags = ast.literal_eval(val) if isinstance(val, str) else val
                    return int(any(t != "O" for t in tags))
                except Exception:
                    return 0
            if "document" in df.columns:
                df = df.groupby("document", as_index=False).first()
            df["label"] = df["labels"].apply(_has_pii)
            return df[["full_text", "label"]].rename(columns={"full_text": "text"}).dropna()

        if "text" in df.columns and "label" in df.columns:
            return df[["text", "label"]].dropna()

        print("[WARN] Kaggle CSV: неизвестный формат, пропускаем.")
        return None

    print("[INFO] Kaggle датасет не найден, обучаем только на синтетических данных.")
    return None


# ── Обучение ──────────────────────────────────────────────────────────────────

def main():
    # 1. Данные
    df = load_synthetic()
    kaggle = load_kaggle()
    if kaggle is not None:
        df = pd.concat([df, kaggle], ignore_index=True)
        print(f"[INFO] Итого записей: {len(df)}")

    X = df["text"].astype(str)
    y = df["label"].astype(int)

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"[INFO] Train: {len(X_train)}  Val: {len(X_val)}")

    # 2. TF-IDF
    print("[INFO] Векторизация TF-IDF...")
    vectorizer = TfidfVectorizer(
        max_features=10_000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=2,
    )
    X_train_v = vectorizer.fit_transform(X_train)
    X_val_v   = vectorizer.transform(X_val)

    # 3. Три модели
    candidates = {
        "LogisticRegression":    LogisticRegression(max_iter=1000, C=1.0, random_state=42),
        "RandomForest":          RandomForestClassifier(n_estimators=200, max_depth=20,
                                                        random_state=42, n_jobs=-1),
        "GradientBoosting":      GradientBoostingClassifier(n_estimators=100, max_depth=5,
                                                             learning_rate=0.1, random_state=42),
    }

    best_name, best_model, best_f1 = None, None, -1.0

    for name, model in candidates.items():
        print(f"[INFO] Обучаем {name}...")
        model.fit(X_train_v, y_train)
        preds = model.predict(X_val_v)
        f1 = f1_score(y_val, preds, average="weighted")
        print(f"       F1 (weighted) = {f1:.4f}")
        if f1 > best_f1:
            best_f1, best_name, best_model = f1, name, model

    # 4. Отчёт для лучшей модели
    print(f"\n{'='*55}")
    print(f"Лучшая модель: {best_name}  (F1={best_f1:.4f})")
    print("="*55)
    y_pred = best_model.predict(X_val_v)
    print(classification_report(y_val, y_pred, target_names=["Без ПД", "С ПД"]))

    # 5. Сохранение
    joblib.dump(best_model, MODEL_PATH)
    joblib.dump(vectorizer, VEC_PATH)
    print(f"[OK] Модель:      {MODEL_PATH}")
    print(f"[OK] Векторайзер: {VEC_PATH}")


if __name__ == "__main__":
    main()
