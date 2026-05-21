"""
Датасет v2 — сложные и граничные случаи для обнаружения ПД.

Фокус на:
  1. Ложные срабатывания — числа похожие на ИИН/карты, но не ПД
  2. Обфусцированные ПД — нестандартное форматирование, пропуски
  3. OCR-шум — замены символов (O→0, I→1, З→3)
  4. Неполные данные — только фамилия, только часть номера
  5. Официальные документы с аббревиатурами
  6. Смешанный язык (KZ + RU в одном тексте)
  7. Жёсткие отрицательные примеры — классные журналы без ПД учеников

Создаёт datasets/synthetic_kz_v2.csv  (5 000 с ПД + 5 000 без ПД = 10 000 записей)
"""
import csv
import json
import os
import random
from datetime import date, timedelta

import pandas as pd

random.seed(2024)

# ── Повторно используем генераторы из v1 ─────────────────────────────────────
from generate_dataset import (
    _make_person, generate_iin, _phone,
    KZ_MALE_NAMES, KZ_FEMALE_NAMES, KZ_SURNAMES_M, KZ_SURNAMES_F,
    RU_MALE_NAMES, RU_FEMALE_NAMES, RU_SURNAMES_M,
    CLASSES, SCHOOLS, BLOOD_TYPES,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ A — ЛОЖНЫЕ СРАБАТЫВАНИЯ (label=0)
# Тексты с числами / именами, которые похожи на ПД, но НЕ являются ПД
# ═══════════════════════════════════════════════════════════════════════════════

FAKE_12DIGIT = [
    # Номера счётов, инвентарные номера — 12 цифр, но не ИИН (не проходят checksum)
    "110000000001", "999999999999", "123456789012", "100000000000",
    "200312345678", "199900010001", "111111111111", "000000000000",
]

FAKE_CARD_PATTERNS = [
    # Похожи на карту, но не проходят Luhn
    "4111 1111 1111 1112", "5500 0000 0000 0005", "3714 4963 5398 432",
    "1234 5678 9012 3456", "9999 8888 7777 6667",
]

FALSE_POSITIVE_TEMPLATES = [
    "Инвентарный номер оборудования: {num12}. Кабинет химии, школа №14.",
    "Серийный номер ноутбука: {num12}. Принят на баланс 01.09.2024.",
    "Артикул товара в накладной: {num12}. Поставщик: ТОО «ШколСнаб».",
    "ID транзакции: {num12}. Оплата за учебники произведена.",
    "Штрихкод учебника: {num12}. Математика, 9 класс.",
    "Договор аренды помещения №{num12} от 15.08.2024.",
    "Код авторизации платежа: {num12}. Статус: выполнено.",
    "Номер заявки на техобслуживание: {num12}.",
]

FALSE_POSITIVE_CARD = [
    "Тестовый номер карты для отладки платёжной системы: {card}.",
    "Пример из документации: номер карты {card} используется только для тестов.",
    "Демо-данные платёжного шлюза: {card} — не является реальной картой.",
]

FALSE_POSITIVE_DATES = [
    "Дата проведения олимпиады: 15.03.2025. Место: актовый зал.",
    "График работы библиотеки: 08.00–17.00 по будням. С 01.09.2024 по 31.05.2025.",
    "Педсовет состоится 28.10.2024 в 15:00 в кабинете директора.",
    "Учебный год начался 02.09.2024 и завершится 31.05.2025.",
    "Классный час запланирован на 14.11.2024, 4-й урок.",
]

FALSE_POSITIVE_NAMES_IN_CONTEXT = [
    "Учитель Иванова О.В. провела открытый урок по биологии.",
    "Директор школы Ахметов Н.С. поздравил коллектив с праздником.",
    "Завуч Петрова Е.А. утвердила расписание экзаменов.",
    "Классный руководитель 9А Касымов Б.Т. сдал отчёт.",
    "Педагог Морозова А.И. победила в конкурсе «Учитель года».",
    "Зав. библиотекой Жаксыбекова Г. приняла 120 учебников.",
    "Методист Сейтқалиев М. провёл семинар для молодых специалистов.",
    "Психолог Волкова Т.Н. провела тренинг по тайм-менеджменту.",
]

FALSE_POSITIVE_PHONE_LIKE = [
    "Телефон горячей линии Министерства: 8-800-080-7777 (бесплатно).",
    "Номер скорой помощи: 103. Пожарная служба: 101. Полиция: 102.",
    "Контактный телефон канцелярии: +7 (717) 272-00-00.",
    "Для записи к врачу звоните: 8-800-080-88-80 (бесплатно, круглосуточно).",
    "Горячая линия по вопросам образования: 8-8000-8-3030.",
]

def _false_positive_record() -> dict:
    category = random.choice(["num12", "card", "date", "teacher_name", "phone_official"])
    if category == "num12":
        num = random.choice(FAKE_12DIGIT)
        tmpl = random.choice(FALSE_POSITIVE_TEMPLATES)
        text = tmpl.format(num12=num)
    elif category == "card":
        card = random.choice(FAKE_CARD_PATTERNS)
        tmpl = random.choice(FALSE_POSITIVE_CARD)
        text = tmpl.format(card=card)
    elif category == "date":
        text = random.choice(FALSE_POSITIVE_DATES)
    elif category == "teacher_name":
        text = random.choice(FALSE_POSITIVE_NAMES_IN_CONTEXT)
    else:
        text = random.choice(FALSE_POSITIVE_PHONE_LIKE)

    # Иногда добавляем нейтральный контекст
    if random.random() < 0.4:
        extra = random.choice([
            " Для справок обращаться в учительскую.",
            " Информация актуальна на текущий учебный год.",
            " Просьба сохранить данный документ.",
        ])
        text += extra

    return {"text": text, "label": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ B — ОБФУСЦИРОВАННЫЕ ПД (label=1)
# ПД записаны нестандартно — сложнее для регулярных выражений
# ═══════════════════════════════════════════════════════════════════════════════

def _obfuscate_iin(iin: str) -> str:
    """Форматирует ИИН нестандартно."""
    styles = [
        iin,
        f"{iin[:6]}-{iin[6:]}",
        f"{iin[:4]} {iin[4:8]} {iin[8:]}",
        f"ИИН: {iin[:3]}.{iin[3:6]}.{iin[6:]}",
        iin.upper(),
    ]
    return random.choice(styles)


def _obfuscate_phone(phone: str) -> str:
    """Форматирует телефон нестандартно."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 11:
        return phone
    styles = [
        f"+7({digits[1:4]}){digits[4:7]}-{digits[7:9]}-{digits[9:11]}",
        f"8{digits[1:4]}{digits[4:7]}{digits[7:11]}",
        f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}",
        f"8-{digits[1:4]}-{digits[4:7]}-{digits[7:9]}-{digits[9:11]}",
        phone,
    ]
    return random.choice(styles)


def _obfuscated_pii_record() -> dict:
    p = _make_person()
    iin_fmt = _obfuscate_iin(p["iin"])
    phone_fmt = _obfuscate_phone(p["phone"])

    templates = [
        f"Данные ученика: {p['fio']} / ИИН {iin_fmt} / тел {phone_fmt}",
        f"[{p['fio_full']}] ИИН={iin_fmt}, дата рождения: {p['dob'].strftime('%d.%m.%Y')}",
        f"ФИО - {p['fio']}; номер - {iin_fmt}; телефон - {phone_fmt}",
        (f"Ученик: {p['fio']}\nИИН: {iin_fmt}\n"
         f"Дата рождения: {p['dob'].strftime('%d.%m.%Y')}\nТелефон: {phone_fmt}"),
        f"{p['fio_full']} (ИИН: {iin_fmt}) обучается в {p['klass']} классе. Тел.: {phone_fmt}.",
    ]
    return {"text": random.choice(templates), "label": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ C — OCR-ШУМ (label=1)
# Сканированные документы с заменами символов
# ═══════════════════════════════════════════════════════════════════════════════

_OCR_SUBS = {
    "О": "0", "о": "0", "З": "3", "з": "3",
    "И": "1", "l": "1", "I": "1", "S": "5",
}


def _add_ocr_noise(text: str, rate: float = 0.05) -> str:
    """Случайно заменяет символы на OCR-артефакты."""
    result = []
    for ch in text:
        if random.random() < rate and ch in _OCR_SUBS:
            result.append(_OCR_SUBS[ch])
        else:
            result.append(ch)
    return "".join(result)


def _ocr_noisy_record() -> dict:
    p = _make_person()
    base = random.choice([
        f"Медицинская карта. ФИО: {p['fio_full']}. ИИН {p['iin']}. Дата рождения {p['dob'].strftime('%d.%m.%Y')}.",
        f"Личное дело учащегося {p['fio']} (ИИН: {p['iin']}). Тел.: {p['phone']}.",
        f"Справка выдана {p['fio_full']}, ИИН {p['iin']}, {p['klass']} класс.",
    ])
    noisy = _add_ocr_noise(base, rate=random.uniform(0.03, 0.12))
    return {"text": noisy, "label": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ D — НЕПОЛНЫЕ ДАННЫЕ (label=1 и label=0)
# Только фамилия, только телефон, только email — граничные случаи
# ═══════════════════════════════════════════════════════════════════════════════

def _partial_pii_record() -> dict:
    """Запись с неполными ПД — достаточно для label=1 (есть идентифицирующая info)."""
    p = _make_person()
    category = random.choice(["iin_only", "phone_name", "email_name", "name_dob"])
    if category == "iin_only":
        text = (
            f"ИИН учащегося: {p['iin']}. "
            f"Документ подан для получения льготного проездного."
        )
    elif category == "phone_name":
        text = (
            f"Контакт для связи: {p['fio']} — {p['phone']}. "
            f"Время звонка: после 17:00."
        )
    elif category == "email_name":
        text = (
            f"Для получения результатов обратиться к {p['fio']}: {p['email']}."
        )
    else:
        text = (
            f"Ученик {p['fio']}, дата рождения {p['dob'].strftime('%d.%m.%Y')}, "
            f"переведён в другую школу."
        )
    return {"text": text, "label": 1}


PARTIAL_NO_PII = [
    "Ученик 7А сдал работу вовремя. Оценка: 5.",
    "Класс 9Б показал лучший результат в олимпиаде по химии.",
    "Двое учеников из 8В выбыли по болезни. Классный руководитель уведомлён.",
    "Команда 11А заняла 1 место на региональном конкурсе.",
    "10Г класс сдаёт пробный ЕНТ в пятницу в 9:00.",
    "Группа учеников посетила выставку 15 ноября.",
    "По итогам четверти: 12 отличников, 24 хорошиста.",
    "Учащиеся 9–11 классов приглашаются на день открытых дверей вуза.",
]


def _partial_no_pii_record() -> dict:
    text = random.choice(PARTIAL_NO_PII)
    if random.random() < 0.3:
        text += " " + random.choice(PARTIAL_NO_PII)
    return {"text": text, "label": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ E — ОФИЦИАЛЬНЫЕ ДОКУМЕНТЫ С АББРЕВИАТУРАМИ (label=1)
# ═══════════════════════════════════════════════════════════════════════════════

def _official_doc_record() -> dict:
    p = _make_person()
    blood = random.choice(BLOOD_TYPES)
    policy_num = "".join(str(random.randint(0, 9)) for _ in range(12))

    templates = [
        (
            f"Ф.И.О.: {p['fio_full']}. И.И.Н.: {p['iin']}. "
            f"Д.Р.: {p['dob'].strftime('%d.%m.%Y')}. Кл.: {p['klass']}. "
            f"Конт. тел.: {p['phone']}. Шк.: {p['school']}."
        ),
        (
            f"Удостоверение личности: {p['fio_full']} (ИИН {p['iin']}). "
            f"Полис ОМС №{policy_num}. Группа крови: {blood}. "
            f"Место учёбы: {p['school']}, {p['klass']} кл."
        ),
        (
            f"ЛИЧНАЯ КАРТОЧКА УЧАЩЕГОСЯ\n"
            f"ФИО: {p['fio_full']}\n"
            f"ИИН: {p['iin']}\n"
            f"Дата рождения: {p['dob'].strftime('%d.%m.%Y')}\n"
            f"Класс: {p['klass']}\n"
            f"Тел. родителей: {p['phone']}\n"
            f"E-mail: {p['email']}\n"
            f"Медицинские сведения: гр. крови {blood}."
        ),
        (
            f"Зачисление в {p['school']}. Уч-ся {p['fio']} "
            f"(ИИН {p['iin']}, {p['dob'].strftime('%d.%m.%Y')} г.р.) "
            f"зачислен(а) в {p['klass']} кл. Тел. {p['phone']}."
        ),
    ]
    return {"text": random.choice(templates), "label": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ F — СМЕШАННЫЙ ЯЗЫК KZ + RU (label=1)
# ═══════════════════════════════════════════════════════════════════════════════

KZ_WORDS = [
    "Оқушы", "Мектеп", "Сынып", "Туған күні", "Телефон",
    "Жеке куәлік", "ЖСН", "Деректер", "Байланыс", "Мекенжай",
]


def _mixed_lang_record() -> dict:
    p = _make_person()
    kz_w1 = random.choice(KZ_WORDS)
    kz_w2 = random.choice(KZ_WORDS)

    templates = [
        (
            f"{kz_w1}: {p['fio_full']}. ЖСН/ИИН: {p['iin']}. "
            f"Туған күні / Дата рождения: {p['dob'].strftime('%d.%m.%Y')}. "
            f"Тел.: {p['phone']}."
        ),
        (
            f"Мектеп: {p['school']}. {kz_w2}: {p['fio']}. "
            f"Сынып / Класс: {p['klass']}. "
            f"ЖСН: {p['iin']}. Байланыс: {p['phone']}."
        ),
        (
            f"Оқушы туралы мәлімет / Сведения об ученике:\n"
            f"ФИО / ТАӘ: {p['fio_full']}\n"
            f"ЖСН (ИИН): {p['iin']}\n"
            f"Сынып (класс): {p['klass']}\n"
            f"Телефон: {p['phone']}"
        ),
    ]
    return {"text": random.choice(templates), "label": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ G — ЖЁСТКИЕ ОТРИЦАТЕЛЬНЫЕ (label=0)
# Педагогические документы без ПД конкретных учеников
# ═══════════════════════════════════════════════════════════════════════════════

HARD_NEGATIVE_TEMPLATES = [
    "Методическое объединение учителей математики. Протокол №{n} от {d}. "
    "Присутствовало {k} учителей. Рассмотрены вопросы подготовки к ЕНТ.",

    "Приказ №{n} от {d}. О проведении итоговой аттестации учащихся {grade} классов. "
    "Экзамены пройдут с {d2} по {d3} согласно утверждённому расписанию.",

    "Акт приёма-передачи учебников. Принято {n} экземпляров учебника «Физика {grade} класс». "
    "Дата: {d}. Подпись завхоза.",

    "Журнал регистрации входящей корреспонденции. "
    "Вх. №{n} от {d}. Отправитель: Управление образования. "
    "Содержание: распоряжение о проведении олимпиады.",

    "Смета расходов на проведение выпускного вечера: "
    "аренда зала — {price1} тг., оформление — {price2} тг., питание — {price3} тг. "
    "Итого: {total} тг.",

    "Годовой план воспитательной работы на {year}-{year2} уч. год. "
    "Раздел 3: патриотическое воспитание. Планируются мероприятия ко Дню Республики, "
    "Наурызу и Дню Победы.",

    "Технический паспорт кабинета №{room}. Площадь: {area} кв.м. "
    "Оборудование: {n} парт, {n2} стульев, 1 доска, проектор. "
    "Последний ремонт: {year} год.",
]


def _hard_negative_record() -> dict:
    d = f"{random.randint(1,28):02d}.{random.randint(1,12):02d}.{random.randint(2023,2025)}"
    d2 = f"{random.randint(1,10):02d}.06.2025"
    d3 = f"{random.randint(11,20):02d}.06.2025"
    text = random.choice(HARD_NEGATIVE_TEMPLATES).format(
        n=random.randint(1, 50),
        d=d, d2=d2, d3=d3,
        k=random.randint(5, 20),
        grade=random.choice(["9", "10", "11"]),
        price1=random.randint(50000, 200000),
        price2=random.randint(20000, 80000),
        price3=random.randint(30000, 100000),
        total=random.randint(100000, 400000),
        year=random.randint(2020, 2024),
        year2=random.randint(2024, 2026),
        room=random.randint(100, 399),
        area=random.randint(40, 90),
        n2=random.randint(20, 35),
    )
    return {"text": text, "label": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════════

GENERATORS_PII = [
    _obfuscated_pii_record,   # 30%
    _obfuscated_pii_record,
    _obfuscated_pii_record,
    _ocr_noisy_record,         # 20%
    _ocr_noisy_record,
    _partial_pii_record,       # 20%
    _partial_pii_record,
    _official_doc_record,      # 15%
    _mixed_lang_record,        # 15%
    _mixed_lang_record,
]

GENERATORS_NO_PII = [
    _false_positive_record,    # 40%
    _false_positive_record,
    _false_positive_record,
    _false_positive_record,
    _hard_negative_record,     # 40%
    _hard_negative_record,
    _hard_negative_record,
    _hard_negative_record,
    _partial_no_pii_record,    # 20%
    _partial_no_pii_record,
]


def main(n_each: int = 5_000):
    out_dir = os.path.join(os.path.dirname(__file__), "datasets")
    os.makedirs(out_dir, exist_ok=True)

    records = []
    for _ in range(n_each):
        gen = random.choice(GENERATORS_PII)
        records.append(gen())
    for _ in range(n_each):
        gen = random.choice(GENERATORS_NO_PII)
        records.append(gen())

    random.shuffle(records)

    csv_path  = os.path.join(out_dir, "synthetic_kz_v2.csv")
    json_path = os.path.join(out_dir, "synthetic_kz_v2.json")

    df = pd.DataFrame(records)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig",
              quoting=csv.QUOTE_NONNUMERIC)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    n_pii = sum(1 for r in records if r["label"] == 1)
    print(f"Датасет v2: {len(records)} записей  (с ПД: {n_pii}, без ПД: {len(records)-n_pii})")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")


if __name__ == "__main__":
    main()
