"""
Оценка сохранённой модели на 10 тестовых примерах (5 с ПД, 5 без).
"""
import os

import joblib
from sklearn.metrics import accuracy_score, f1_score

BASE_DIR   = os.path.dirname(__file__)
MODEL_PATH = os.path.join(BASE_DIR, "pii_model.pkl")
VEC_PATH   = os.path.join(BASE_DIR, "vectorizer.pkl")

# ── Тестовые примеры ──────────────────────────────────────────────────────────
# (текст, ожидаемый label)  label=1 → содержит ПД, 0 → не содержит

EXAMPLES = [
    # ── С персональными данными ──────────────────────────────────────────────
    (
        "Ахметов Нурлан Серікович, ИИН 050314312340, класс 7В, "
        "телефон родителей +77051234567",
        1,
    ),
    (
        "ФИО: Иванова Мария Петровна. Дата рождения: 12.03.2008. "
        "ИИН: 080312612351. Email: maria.ivanova@gmail.com",
        1,
    ),
    (
        "Контактные данные ученицы: Жуматова Асель, тел. +77751234567, "
        "email asel.zhumatova@mail.ru, класс 9А",
        1,
    ),
    (
        "Личное дело. Бекзат Алиев, ИИН 100615523456, "
        "дата рождения 15.06.2010, тел. +77081112233",
        1,
    ),
    (
        "Анкета ученика: Динара Нурмагамбетова | +77011234567 | "
        "dinara@yandex.ru | ИИН: 071220612349 | 10Б класс",
        1,
    ),
    # ── Без персональных данных ──────────────────────────────────────────────
    (
        "Контрольная работа по алгебре пройдёт в пятницу в кабинете 14.",
        0,
    ),
    (
        "Школьная столовая обновила меню на эту неделю.",
        0,
    ),
    (
        "Олимпиада по информатике состоится в следующем месяце в городском центре.",
        0,
    ),
    (
        "Классный руководитель объявил о предстоящей экскурсии в краеведческий музей.",
        0,
    ),
    (
        "Итоги ЕНТ будут опубликованы на официальном сайте школы в июне.",
        0,
    ),
]


def main():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(VEC_PATH):
        print("[ERROR] Модель не найдена. Сначала запустите: python train_model.py")
        return

    model      = joblib.load(MODEL_PATH)
    vectorizer = joblib.load(VEC_PATH)

    texts       = [e[0] for e in EXAMPLES]
    true_labels = [e[1] for e in EXAMPLES]

    X    = vectorizer.transform(texts)
    preds = model.predict(X)
    probs = model.predict_proba(X)[:, 1]   # вероятность класса 1 (ПД)

    print("=" * 70)
    print(f"{'ОЦЕНКА МОДЕЛИ НА ТЕСТОВЫХ ПРИМЕРАХ':^70}")
    print("=" * 70)

    for i, (text, true) in enumerate(EXAMPLES):
        pred   = preds[i]
        prob   = probs[i]
        ok     = pred == true
        status = "OK" if ok else "!!"
        t_str  = "ПД"    if true == 1 else "Без ПД"
        p_str  = "ПД"    if pred == 1 else "Без ПД"

        short = text if len(text) <= 65 else text[:62] + "..."
        print(f"\n[{status}] Пример {i + 1}")
        print(f"  Текст:       {short}")
        print(f"  Истина:      {t_str}")
        print(f"  Предсказано: {p_str}  (P(ПД) = {prob:.2%})")

    print("\n" + "=" * 70)
    acc = accuracy_score(true_labels, preds)
    f1  = f1_score(true_labels, preds, average="weighted", zero_division=0)
    print(f"  Accuracy : {acc:.2%}  ({sum(p==t for p,t in zip(preds,true_labels))}/10 верных)")
    print(f"  F1-score : {f1:.4f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
