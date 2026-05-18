"""
Генератор синтетических ПД казахстанских школьников.
Создаёт 10 000 базовых записей + 1 000 контекстных записей (50%/50% с ПД и без)
и сохраняет результат в CSV и JSON.
"""
import csv
import json
import os
import random
from datetime import date, timedelta

import pandas as pd

random.seed(42)

# ── Имена ─────────────────────────────────────────────────────────────────────

KZ_MALE_NAMES = [
    "Нурлан", "Дамир", "Бекзат", "Ерлан", "Серик", "Арман",
    "Алмас", "Тимур", "Айбек", "Данияр", "Жанибек", "Санжар",
    "Алдияр", "Бауыржан", "Ернар", "Қайрат", "Мирас", "Нұрберген",
    "Асылбек", "Нурсултан", "Жасулан", "Бахыт", "Ерназар", "Улан",
]
KZ_FEMALE_NAMES = [
    "Айгерим", "Асель", "Алия", "Жанар", "Гульнара", "Динара",
    "Меруерт", "Айгуль", "Зарина", "Камила", "Дана", "Айдана",
    "Ақерке", "Гүлназ", "Жұлдыз", "Малика", "Назерке", "Сабина",
    "Ұлбосын", "Ботакөз", "Толқын", "Перизат", "Аружан", "Гаухар",
]
KZ_SURNAMES_M = [
    "Ахметов", "Жуматов", "Мамытов", "Бекмагамбетов", "Нурмагамбетов",
    "Алиев", "Сатыбалдиев", "Касымов", "Сейтқалиев", "Əбілов",
    "Джаксыбеков", "Байжанов", "Омаров", "Тасбеков", "Нурланов",
    "Серіков", "Қасымов", "Тоқтаров", "Əбенов", "Жаксыбеков",
]
KZ_SURNAMES_F = [
    (s.rstrip("ов") + "ова") if s.endswith("ов")
    else (s.rstrip("ев") + "ева") if s.endswith("ев")
    else s + "а"
    for s in KZ_SURNAMES_M
]

RU_MALE_NAMES = [
    "Иван", "Александр", "Дмитрий", "Михаил", "Андрей",
    "Сергей", "Максим", "Алексей", "Николай", "Виктор",
]
RU_FEMALE_NAMES = [
    "Мария", "Анна", "Елена", "Ольга", "Наталья",
    "Татьяна", "Юлия", "Светлана", "Ирина", "Екатерина",
]
RU_SURNAMES_M = [
    "Иванов", "Петров", "Сидоров", "Кузнецов", "Попов",
    "Волков", "Морозов", "Зайцев", "Козлов", "Лебедев",
]
RU_SURNAMES_F = [s[:-2] + "ва" if s.endswith("ов") else s[:-2] + "ева" if s.endswith("ев") else s + "а"
                 for s in RU_SURNAMES_M]
RU_PATRONYMICS_M = [
    "Иванович", "Александрович", "Сергеевич",
    "Николаевич", "Петрович", "Дмитриевич", "Андреевич",
]
RU_PATRONYMICS_F = [
    "Ивановна", "Александровна", "Сергеевна",
    "Николаевна", "Петровна", "Дмитриевна", "Андреевна",
]

KZ_PHONE_CODES = [
    "700", "701", "702", "705", "707",
    "708", "747", "771", "775", "776", "777", "778",
]

CLASSES = [f"{g}{l}" for g in range(1, 12) for l in "АБВ"]

SCHOOLS = [
    "Средняя школа №14 г. Алматы",
    "Гимназия №37 г. Астаны",
    "НИШ физико-математического направления",
    "Школа-лицей №5 г. Шымкента",
    "Общеобразовательная школа №102",
    "Школа-гимназия «Білім-Инновация»",
    "КГУ «Средняя школа №23»",
]

BLOOD_TYPES = ["I+", "I−", "II+", "II−", "III+", "III−", "IV+", "IV−"]

DIAGNOSES = [
    "практически здоров", "хронический ринит", "нарушение осанки",
    "миопия слабой степени", "аллергический ринит", "плоскостопие I ст.",
]

NEUTRAL_TEXTS = [
    "Контрольная работа по математике прошла успешно.",
    "Расписание уроков на следующую неделю изменено.",
    "Сегодня в школе провели олимпиаду по физике.",
    "Классный час посвящён теме безопасного поведения в сети.",
    "Школьная библиотека пополнена новыми учебниками.",
    "В актовом зале состоялось торжественное собрание.",
    "Результаты контрольного среза будут объявлены завтра.",
    "Учитель объяснил новую тему по химии.",
    "Спортивные соревнования между классами запланированы на пятницу.",
    "Домашнее задание по литературе: прочитать главы 5–7.",
    "Педагогический совет обсудил итоги четверти.",
    "Экскурсия в краеведческий музей состоится в среду.",
    "Учебный план на второй семестр утверждён директором.",
    "Родительское собрание пройдёт в четверг в 18:00.",
    "Кабинет информатики оборудован новыми компьютерами.",
    "Дежурный класс приготовил концерт ко Дню учителя.",
    "Итоговые оценки будут выставлены до конца месяца.",
    "На уроке биологии изучали строение клетки.",
    "Школьная команда заняла первое место в районе.",
    "Директор поздравил учеников с Днём знаний.",
    "Методическое объединение учителей состоится в пятницу.",
    "Все ученики получили учебники на новый учебный год.",
    "Спортивный праздник пройдёт на стадионе школы.",
    "Кружок робототехники принимает новых участников.",
]

NEUTRAL_CONTEXTUAL = [
    "Протокол заседания педагогического совета от {date}. Присутствовало {n} педагогов. Обсуждены вопросы учебно-воспитательной работы.",
    "Объявление: столовая школы переходит на новое меню с {date}. Стоимость обеда составит {price} тенге.",
    "Результаты олимпиады по математике среди учеников 9–11 классов. Победители будут награждены на линейке.",
    "Уведомление родителям: в связи с карантинными мерами занятия переводятся в дистанционный формат.",
    "Расписание экзаменов на конец учебного года опубликовано на стенде школы.",
    "Школа получила грант на развитие STEM-образования в размере 500 000 тенге.",
    "Внимание! Замена уроков в {klass} классе: вместо физики будет математика.",
    "Итоги четверти: средний балл по школе составил {score}. Отличников — {n} человек.",
]


# ── ИИН ──────────────────────────────────────────────────────────────────────

def _iin_checksum(base11: str):
    d = [int(c) for c in base11]
    s1 = sum(d[i] * (i + 1) for i in range(11)) % 11
    if s1 < 10:
        return s1
    s2 = sum(d[i] * ((i + 3) % 11 or 11) for i in range(11)) % 11
    if s2 < 10:
        return s2
    return None


def generate_iin(birth_date: date) -> str:
    yy = birth_date.strftime("%y")
    mm = birth_date.strftime("%m")
    dd = birth_date.strftime("%d")
    for _ in range(100):
        seq4 = str(random.randint(1000, 9999))
        base = yy + mm + dd + "3" + seq4
        chk = _iin_checksum(base)
        if chk is not None:
            return base + str(chk)
    return yy + mm + dd + "3" + "12345"


# ── Генераторы полей ──────────────────────────────────────────────────────────

def _phone() -> str:
    code = random.choice(KZ_PHONE_CODES)
    digits = "".join(str(random.randint(0, 9)) for _ in range(7))
    fmt = random.choice([
        f"+7{code}{digits}",
        f"+7 {code} {digits[:3]}-{digits[3:5]}-{digits[5:]}",
        f"+7({code}){digits[:3]}-{digits[3:5]}-{digits[5:]}",
        f"87{code[-2:]}{digits}",
    ])
    return fmt


def _birth_date() -> date:
    start = date(2005, 1, 1)
    return start + timedelta(
        days=random.randint(0, (date(2015, 12, 31) - start).days)
    )


def _email(first: str, last: str) -> str:
    domain = random.choice(["gmail.com", "mail.ru", "yandex.ru", "bk.ru"])
    f_ascii = "".join(c for c in first.lower() if c.isascii() and c.isalpha()) or "user"
    l_ascii = "".join(c for c in last.lower() if c.isascii() and c.isalpha()) or "school"
    sep = random.choice([".", "_", ""])
    num = random.choice(["", str(random.randint(1, 99))])
    return f"{f_ascii}{sep}{l_ascii}{num}@{domain}"


def _make_person():
    is_kz = random.random() < 0.6
    gender = random.choice(["M", "F"])
    if is_kz:
        first = random.choice(KZ_MALE_NAMES if gender == "M" else KZ_FEMALE_NAMES)
        last = random.choice(KZ_SURNAMES_M if gender == "M" else KZ_SURNAMES_F)
        fio = f"{last} {first}"
        fio_full = fio
    else:
        first = random.choice(RU_MALE_NAMES if gender == "M" else RU_FEMALE_NAMES)
        last = random.choice(RU_SURNAMES_M if gender == "M" else RU_SURNAMES_F)
        patr = random.choice(RU_PATRONYMICS_M if gender == "M" else RU_PATRONYMICS_F)
        fio = f"{last} {first}"
        fio_full = f"{last} {first} {patr}"
    dob = _birth_date()
    iin = generate_iin(dob)
    phone = _phone()
    email = _email(first, last)
    klass = random.choice(CLASSES)
    school = random.choice(SCHOOLS)
    return dict(
        first=first, last=last, fio=fio, fio_full=fio_full,
        dob=dob, iin=iin, phone=phone, email=email,
        klass=klass, school=school,
    )


# ── Базовые записи с ПД ───────────────────────────────────────────────────────

def _record_with_pii() -> dict:
    p = _make_person()
    templates = [
        f"Ученик {p['fio']}, ИИН {p['iin']}, класс {p['klass']}, телефон родителей {p['phone']}",
        f"ФИО: {p['fio_full']}. Дата рождения: {p['dob'].strftime('%d.%m.%Y')}. ИИН: {p['iin']}. Email: {p['email']}",
        f"Контактные данные: {p['fio']}, тел. {p['phone']}, email {p['email']}, класс {p['klass']}",
        f"Личное дело. {p['fio_full']}, {p['dob'].strftime('%d.%m.%Y')}, ИИН {p['iin']}, {p['klass']} класс",
        f"Анкета: {p['fio']} | {p['phone']} | {p['email']} | ИИН: {p['iin']}",
        f"{p['klass']} класс. {p['fio']}, дата рождения {p['dob'].strftime('%d.%m.%Y')}, тел.: {p['phone']}",
    ]
    return {
        "fio": p["fio"], "iin": p["iin"], "phone": p["phone"],
        "birth_date": p["dob"].strftime("%d.%m.%Y"),
        "email": p["email"], "school_class": p["klass"],
        "text": random.choice(templates), "label": 1,
    }


# ── Базовые записи без ПД ────────────────────────────────────────────────────

def _record_without_pii() -> dict:
    base = random.choice(NEUTRAL_TEXTS)
    variations = [
        base,
        f"{base} Класс показал хороший результат.",
        f"Сегодня в школе: {base[0].lower() + base[1:]}",
        base + " " + random.choice(NEUTRAL_TEXTS),
    ]
    return {
        "fio": "", "iin": "", "phone": "",
        "birth_date": "", "email": "", "school_class": "",
        "text": random.choice(variations), "label": 0,
    }


# ── НОВЫЕ контекстные записи с ПД ────────────────────────────────────────────

def _contextual_school_certificate(p: dict) -> str:
    """Справка из школы."""
    variants = [
        (
            f"СПРАВКА. Выдана {p['fio_full']}, ИИН {p['iin']}, "
            f"дата рождения {p['dob'].strftime('%d.%m.%Y')}, в том, что он(а) является учеником(цей) "
            f"{p['klass']} класса {p['school']}. "
            f"Для предъявления по месту требования. "
            f"Телефон для связи: {p['phone']}."
        ),
        (
            f"Настоящая справка выдана ученику {p['klass']} класса "
            f"{p['school']} — {p['fio']}, "
            f"дата рождения {p['dob'].strftime('%d.%m.%Y')}, ИИН: {p['iin']}, "
            f"в подтверждение того, что он(а) обучается в данном учебном заведении "
            f"в {date.today().year} учебном году."
        ),
        (
            f"Справка об обучении. ФИО: {p['fio_full']}. "
            f"Класс: {p['klass']}. ИИН: {p['iin']}. "
            f"Дата рождения: {p['dob'].strftime('%d.%m.%Y')}. "
            f"Контактный телефон родителей: {p['phone']}. "
            f"Место учёбы: {p['school']}."
        ),
    ]
    return random.choice(variants)


def _contextual_medical_card(p: dict) -> str:
    """Медицинская карта школьника."""
    blood = random.choice(BLOOD_TYPES)
    diag = random.choice(DIAGNOSES)
    variants = [
        (
            f"МЕДИЦИНСКАЯ КАРТА УЧАЩЕГОСЯ. "
            f"ФИО: {p['fio_full']}. "
            f"Дата рождения: {p['dob'].strftime('%d.%m.%Y')}. "
            f"ИИН: {p['iin']}. "
            f"Класс: {p['klass']}. Школа: {p['school']}. "
            f"Группа крови: {blood}. "
            f"Телефон родителей: {p['phone']}. "
            f"Диагноз: {diag}."
        ),
        (
            f"Медицинская карта. Пациент: {p['fio']}, "
            f"{p['dob'].strftime('%d.%m.%Y')} г.р. "
            f"ИИН пациента: {p['iin']}. "
            f"Телефон: {p['phone']}. "
            f"Учится в {p['klass']} классе, {p['school']}. "
            f"Группа крови и резус-фактор: {blood}. "
            f"Заключение: {diag}."
        ),
        (
            f"Карта здоровья обучающегося. "
            f"Учащийся: {p['fio_full']} (ИИН {p['iin']}), "
            f"рождён(а) {p['dob'].strftime('%d.%m.%Y')}. "
            f"Класс: {p['klass']}. "
            f"Контакт: {p['phone']}, {p['email']}. "
            f"Диагноз: {diag}. Гр. крови: {blood}."
        ),
    ]
    return random.choice(variants)


def _contextual_application(p: dict) -> str:
    """Заявление от родителей."""
    year = date.today().year
    # Создаём данные родителя
    parent_fio = f"{random.choice(KZ_SURNAMES_M if random.random() > 0.5 else KZ_SURNAMES_F)} " \
                 f"{random.choice(KZ_MALE_NAMES if random.random() > 0.5 else KZ_FEMALE_NAMES)}"
    parent_iin_date = date(1975, 1, 1) + timedelta(days=random.randint(0, 5000))
    parent_iin = generate_iin(parent_iin_date)
    variants = [
        (
            f"ЗАЯВЛЕНИЕ. Директору {p['school']}. "
            f"От родителя (законного представителя): {parent_fio}, ИИН {parent_iin}, "
            f"тел. {p['phone']}. "
            f"Прошу принять моего ребёнка — {p['fio_full']}, "
            f"дата рождения {p['dob'].strftime('%d.%m.%Y')}, ИИН {p['iin']}, "
            f"в {p['klass']} класс на {year}/{year+1} учебный год."
        ),
        (
            f"Заявление о переводе учащегося. "
            f"Я, {parent_fio} (ИИН {parent_iin}), родитель ученика {p['klass']} класса "
            f"{p['fio_full']} (ИИН {p['iin']}, дата рождения {p['dob'].strftime('%d.%m.%Y')}), "
            f"прошу перевести моего ребёнка в другую школу по семейным обстоятельствам. "
            f"Контактный телефон: {p['phone']}."
        ),
        (
            f"Директору {p['school']} от {parent_fio}. "
            f"Заявление. Прошу выдать справку для ребёнка: {p['fio']}, "
            f"ИИН {p['iin']}, {p['dob'].strftime('%d.%m.%Y')} г.р., {p['klass']} кл. "
            f"Телефон: {p['phone']}. Email: {p['email']}."
        ),
    ]
    return random.choice(variants)


def _contextual_grade_report(p: dict) -> str:
    """Ведомость успеваемости / характеристика."""
    grades = {s: random.randint(3, 5) for s in
              ["математика", "русский язык", "физика", "биология", "история", "география"]}
    grade_str = ", ".join(f"{s}: {g}" for s, g in grades.items())
    avg = sum(grades.values()) / len(grades)
    variants = [
        (
            f"Ведомость успеваемости. Ученик: {p['fio_full']}, "
            f"ИИН {p['iin']}, {p['klass']} класс, {p['school']}. "
            f"Оценки за четверть: {grade_str}. "
            f"Средний балл: {avg:.1f}. "
            f"Телефон родителей: {p['phone']}."
        ),
        (
            f"Характеристика на учащегося {p['fio_full']} "
            f"(ИИН {p['iin']}, дата рождения {p['dob'].strftime('%d.%m.%Y')}), "
            f"{p['klass']} класс {p['school']}. "
            f"Контакт: {p['phone']}, {p['email']}. "
            f"Успеваемость: {grade_str}. Средний балл: {avg:.1f}."
        ),
    ]
    return random.choice(variants)


def _contextual_parent_contact_list(p: dict) -> str:
    """Список контактов родителей класса."""
    return (
        f"Список контактов родителей {p['klass']} класса, {p['school']}. "
        f"{p['fio_full']} — ИИН: {p['iin']}, "
        f"дата рождения: {p['dob'].strftime('%d.%m.%Y')}, "
        f"телефон: {p['phone']}, email: {p['email']}."
    )


def _contextual_insurance_policy(p: dict) -> str:
    """Страховой полис / льготы."""
    policy_num = "".join(str(random.randint(0, 9)) for _ in range(10))
    return (
        f"Страховой полис №{policy_num}. "
        f"Застрахованный: {p['fio_full']}, ИИН {p['iin']}, "
        f"дата рождения {p['dob'].strftime('%d.%m.%Y')}. "
        f"Контактный телефон: {p['phone']}. "
        f"Место обучения: {p['school']}, {p['klass']} класс."
    )


_CONTEXTUAL_GENERATORS_PII = [
    _contextual_school_certificate,
    _contextual_medical_card,
    _contextual_application,
    _contextual_grade_report,
    _contextual_parent_contact_list,
    _contextual_insurance_policy,
]


def _record_contextual_pii() -> dict:
    """1 из 1000 новых реалистичных записей с ПД."""
    p = _make_person()
    gen = random.choice(_CONTEXTUAL_GENERATORS_PII)
    text = gen(p)
    return {
        "fio": p["fio"], "iin": p["iin"], "phone": p["phone"],
        "birth_date": p["dob"].strftime("%d.%m.%Y"),
        "email": p["email"], "school_class": p["klass"],
        "text": text, "label": 1,
    }


def _record_contextual_no_pii() -> dict:
    """1 из 1000 новых контекстных записей БЕЗ ПД."""
    template = random.choice(NEUTRAL_CONTEXTUAL)
    text = template.format(
        date=f"{random.randint(1, 28):02d}.{random.randint(1, 12):02d}.{date.today().year}",
        n=random.randint(10, 40),
        price=random.choice([250, 350, 450, 500]),
        klass=random.choice(CLASSES),
        score=round(random.uniform(3.2, 4.8), 1),
    )
    noise = random.choice(NEUTRAL_TEXTS)
    full = random.choice([text, f"{text} {noise}", noise])
    return {
        "fio": "", "iin": "", "phone": "",
        "birth_date": "", "email": "", "school_class": "",
        "text": full, "label": 0,
    }


# ── Точка входа ───────────────────────────────────────────────────────────────

def main(n_total: int = 10_000, n_contextual: int = 1_000):
    out_dir = os.path.join(os.path.dirname(__file__), "datasets")
    os.makedirs(out_dir, exist_ok=True)

    # Базовые записи
    records = [_record_with_pii() for _ in range(n_total // 2)]
    records += [_record_without_pii() for _ in range(n_total // 2)]

    # Новые контекстные записи (50% с ПД, 50% без)
    half_ctx = n_contextual // 2
    records += [_record_contextual_pii() for _ in range(half_ctx)]
    records += [_record_contextual_no_pii() for _ in range(n_contextual - half_ctx)]

    random.shuffle(records)

    csv_path = os.path.join(out_dir, "synthetic_kz_students.csv")
    json_path = os.path.join(out_dir, "synthetic_kz_students.json")

    df = pd.DataFrame(records)
    for col in ("iin", "phone"):
        df[col] = df[col].astype(str).str.strip()
    df.to_csv(
        csv_path, index=False, encoding="utf-8-sig",
        quoting=csv.QUOTE_NONNUMERIC,
    )
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    n_pii = sum(1 for r in records if r["label"] == 1)
    print(f"Сгенерировано {len(records)} записей.")
    print(f"  Базовые с ПД:      {n_total // 2}")
    print(f"  Базовые без ПД:    {n_total // 2}")
    print(f"  Контекстные с ПД:  {half_ctx}")
    print(f"  Контекстные б/ПД:  {n_contextual - half_ctx}")
    print(f"  Итого с ПД:        {n_pii}")
    print(f"  Итого без ПД:      {len(records) - n_pii}")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")


if __name__ == "__main__":
    main()
