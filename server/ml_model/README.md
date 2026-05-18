# ML-модель для обнаружения ПД казахстанских школьников

## Быстрый старт

### 1. Установить зависимости
```bash
pip install faker pandas scikit-learn joblib
```

### 2. Сгенерировать синтетический датасет (10 000 записей)
```bash
python generate_dataset.py
```
Создаёт два файла в `datasets/`:
- `synthetic_kz_students.csv`
- `synthetic_kz_students.json`

### 3. Обучить модель
```bash
python train_model.py
```
Обучает LogisticRegression, RandomForest, GradientBoosting — сохраняет лучшую по F1.
Сохраняет: `pii_model.pkl`, `vectorizer.pkl`

### 4. Оценить модель на тестовых примерах
```bash
python evaluate_model.py
```

---

## Добавить Kaggle датасет (опционально, улучшает качество)

Датасет: **pii-detection-removal-from-educational-data**

1. Зарегистрируйся на [kaggle.com](https://kaggle.com)
2. Скачай `kaggle.json`: Account → API → **Create New Token**
3. Положи `kaggle.json` в папку `C:/Users/ИМЯ/.kaggle/`
4. Выполни:
```bash
pip install kaggle
kaggle competitions download -c pii-detection-removal-from-educational-data
```
5. Распакуй архив и положи `train.csv` сюда:
```
server/ml_model/datasets/pii_detection_train.csv
```
6. Повторно запусти `train_model.py` — он подхватит оба датасета автоматически.

---

## Структура файлов

```
server/ml_model/
├── datasets/
│   ├── synthetic_kz_students.csv   # сгенерированный датасет
│   ├── synthetic_kz_students.json  # то же в JSON
│   └── pii_detection_train.csv     # Kaggle (добавить вручную)
├── generate_dataset.py             # генератор синтетических данных
├── train_model.py                  # обучение модели
├── evaluate_model.py               # оценка на примерах
├── pii_model.pkl                   # сохранённая модель (после обучения)
├── vectorizer.pkl                  # TF-IDF векторайзер (после обучения)
└── README.md
```
