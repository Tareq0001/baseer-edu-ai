"""SQLite WAL persistence, CSV/JSON ETL validation, and anonymized Digital Skills seeder."""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

VALID_INTERVENTION_STATUSES = {"PLANNED", "IN_PROGRESS", "COMPLETED"}
DEFAULT_TEACHER_NAME = "أ. طارق ابوعشي"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and foreign keys."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create tables and analytical indexes if they do not exist."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    grade_level TEXT NOT NULL,
                    cohort_term TEXT NOT NULL DEFAULT 'Term 2',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
                    assessment_title TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                    max_score REAL NOT NULL DEFAULT 100 CHECK (max_score > 0),
                    assessment_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interventions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
                    teacher_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PLANNED',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_assessments_student_date
                    ON assessments(student_id, assessment_date);

                CREATE INDEX IF NOT EXISTS idx_assessments_skill
                    ON assessments(skill);

                CREATE INDEX IF NOT EXISTS idx_interventions_student_status
                    ON interventions(student_id, status);
                """
            )
    finally:
        conn.close()


def _validate_iso_date(date_str: str) -> str:
    cleaned = str(date_str or "").strip()
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid assessment_date '{cleaned}' (expected YYYY-MM-DD)") from exc
    return cleaned


def validate_assessment_record(raw: dict[str, Any], row_index: int = 1) -> dict[str, Any]:
    """Validate and normalize a single assessment record."""
    student_id = str(raw.get("student_id") or "").strip()
    if not student_id:
        raise ValueError(f"Row {row_index}: student_id is required")

    full_name = str(raw.get("full_name") or f"Learner {student_id}").strip()
    grade_level = str(raw.get("grade_level") or "الصف الأول متوسط").strip()
    skill = str(raw.get("skill") or "").strip()
    if not skill:
        raise ValueError(f"Row {row_index}: skill is required")

    try:
        raw_score = float(raw["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Row {row_index}: numeric score is required") from exc

    try:
        max_score = float(raw.get("max_score", 100.0) or 100.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {row_index}: max_score must be numeric") from exc

    if max_score <= 0:
        raise ValueError(f"Row {row_index}: max_score must be > 0")
    if raw_score < 0 or raw_score > max_score:
        raise ValueError(
            f"Row {row_index}: score {raw_score} out of valid range [0, {max_score}]"
        )

    normalized_pct = round((raw_score / max_score) * 100.0, 2)
    assessment_date = _validate_iso_date(str(raw.get("assessment_date") or ""))
    assessment_title = str(
        raw.get("assessment_title") or f"{skill} Checkpoint"
    ).strip()
    cohort_term = str(raw.get("cohort_term") or "Term 2").strip()

    return {
        "student_id": student_id,
        "full_name": full_name,
        "grade_level": grade_level,
        "cohort_term": cohort_term,
        "assessment_title": assessment_title,
        "skill": skill,
        "score": normalized_pct,
        "max_score": 100.0,
        "assessment_date": assessment_date,
    }


def parse_csv_records(csv_text: str) -> list[dict[str, Any]]:
    """Parse and validate CSV text into normalized assessment records."""
    text = (csv_text or "").strip()
    if not text:
        raise ValueError("CSV payload is empty")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV header row is missing")

    required = {"student_id", "skill", "score", "assessment_date"}
    present = {col.strip() for col in reader.fieldnames if col}
    missing = sorted(required - present)
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(missing)}")

    parsed: list[dict[str, Any]] = []
    for idx, row in enumerate(reader, start=2):
        normalized_row = {
            k.strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }
        parsed.append(validate_assessment_record(normalized_row, row_index=idx))

    if not parsed:
        raise ValueError("CSV contains header only and zero data rows")
    return parsed


def ingest_assessment_records(
    db_path: str | Path,
    records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and batch-insert assessment records inside a single SQLite transaction."""
    if not records:
        raise ValueError("At least one assessment record is required")

    validated = [
        validate_assessment_record(rec, row_index=idx)
        for idx, rec in enumerate(records, start=1)
    ]

    now_iso = _utc_now_iso()
    conn = get_connection(db_path)
    try:
        with conn:
            seen_students: set[str] = set()
            for item in validated:
                if item["student_id"] not in seen_students:
                    conn.execute(
                        """
                        INSERT INTO students (student_id, full_name, grade_level, cohort_term, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(student_id) DO UPDATE SET
                            full_name = excluded.full_name,
                            grade_level = excluded.grade_level,
                            cohort_term = excluded.cohort_term
                        """,
                        (
                            item["student_id"],
                            item["full_name"],
                            item["grade_level"],
                            item["cohort_term"],
                            now_iso,
                        ),
                    )
                    seen_students.add(item["student_id"])

                conn.execute(
                    """
                    INSERT INTO assessments (
                        student_id, assessment_title, skill, score, max_score, assessment_date, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["student_id"],
                        item["assessment_title"],
                        item["skill"],
                        item["score"],
                        item["max_score"],
                        item["assessment_date"],
                        now_iso,
                    ),
                )
    finally:
        conn.close()

    return {
        "ingested_count": len(validated),
        "students_affected": len({item["student_id"] for item in validated}),
    }


def list_all_students(db_path: str | Path) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT student_id, full_name, grade_level, cohort_term, created_at FROM students ORDER BY student_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_assessments(db_path: str | Path) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, student_id, assessment_title, skill, score, max_score, assessment_date, created_at
            FROM assessments
            ORDER BY assessment_date ASC, id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_student(db_path: str | Path, student_id: str) -> dict[str, Any] | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT student_id, full_name, grade_level, cohort_term, created_at FROM students WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_student_assessments(db_path: str | Path, student_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, student_id, assessment_title, skill, score, max_score, assessment_date, created_at
            FROM assessments
            WHERE student_id = ?
            ORDER BY assessment_date ASC, id ASC
            """,
            (student_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_intervention(
    db_path: str | Path,
    student_id: str,
    action_type: str,
    notes: str,
    status: str = "PLANNED",
    teacher_name: str = DEFAULT_TEACHER_NAME,
) -> dict[str, Any]:
    """Persist a teacher intervention action for an existing student."""
    normalized_status = (status or "PLANNED").strip().upper()
    if normalized_status not in VALID_INTERVENTION_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of {sorted(VALID_INTERVENTION_STATUSES)}"
        )

    cleaned_action = (action_type or "").strip()
    cleaned_notes = (notes or "").strip()
    if not cleaned_action or not cleaned_notes:
        raise ValueError("action_type and notes are required")

    now_iso = _utc_now_iso()
    conn = get_connection(db_path)
    try:
        student = conn.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        if not student:
            raise LookupError(f"Student '{student_id}' not found")

        with conn:
            cursor = conn.execute(
                """
                INSERT INTO interventions (student_id, teacher_name, action_type, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    (teacher_name or DEFAULT_TEACHER_NAME).strip(),
                    cleaned_action,
                    cleaned_notes,
                    normalized_status,
                    now_iso,
                ),
            )
            row = conn.execute(
                "SELECT * FROM interventions WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)
    finally:
        conn.close()


def list_interventions(
    db_path: str | Path,
    student_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        if student_id:
            rows = conn.execute(
                """
                SELECT id, student_id, teacher_name, action_type, notes, status, created_at
                FROM interventions
                WHERE student_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (student_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, student_id, teacher_name, action_type, notes, status, created_at
                FROM interventions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _build_demo_records() -> list[dict[str, Any]]:
    """Return anonymized synthetic Digital Skills cohort records across middle-school grades."""
    cohort_profiles = [
        # (student_id, full_name, grade_level, [(skill, title, score, date), ...])
        (
            "DS-101",
            "متعلم نموذجي #101",
            "الصف الأول متوسط",
            [
                ("Data & Spreadsheets", "جداول البيانات والدوال", 68, "2026-09-07"),
                ("Cyber Hygiene", "السلامة الرقمية وكلمات المرور", 62, "2026-09-14"),
                ("Network Fundamentals", "أساسيات الشبكات", 50, "2026-09-21"),
                ("Python Logic", "المتغيرات والشروط في بايثون", 42, "2026-09-28"),
                ("Algorithms", "مخططات التدفق والخوارزميات", 36, "2026-10-05"),
            ],
        ),
        (
            "DS-102",
            "متعلم نموذجي #102",
            "الصف الثاني متوسط",
            [
                ("Python Logic", "الحلقات التكرارية", 74, "2026-09-07"),
                ("Network Fundamentals", "عناوين IP والحزم", 58, "2026-09-14"),
                ("Algorithms", "البحث الخطي والثنائي", 48, "2026-09-21"),
                ("Python Logic", "الدوال والمعاملات", 44, "2026-09-28"),
            ],
        ),
        (
            "DS-103",
            "متعلم نموذجي #103",
            "الصف الثالث متوسط",
            [
                ("Cyber Hygiene", "حماية الحسابات", 54, "2026-09-08"),
                ("Python Logic", "القوائم والعمليات", 49, "2026-09-15"),
                ("Algorithms", "تحليل الخوارزميات", 45, "2026-09-22"),
            ],
        ),
        (
            "DS-104",
            "متعلم نموذجي #104",
            "الصف الأول متوسط",
            [
                ("Data & Spreadsheets", "الدوال المنطقية IF", 78, "2026-09-07"),
                ("Cyber Hygiene", "التصيد الإلكتروني", 82, "2026-09-14"),
                ("Network Fundamentals", "طوبولوجيا الشبكات", 55, "2026-09-21"),
                ("Python Logic", "تتبع الأكواد", 52, "2026-09-28"),
                ("Algorithms", "تصميم الخوارزمية", 50, "2026-10-05"),
            ],
        ),
        (
            "DS-105",
            "متعلم نموذجي #105",
            "الصف الثاني متوسط",
            [
                ("Python Logic", "أساسيات بايثون", 58, "2026-09-07"),
                ("Network Fundamentals", "بروتوكول HTTP", 64, "2026-09-14"),
                ("Data & Spreadsheets", "الرسوم البيانية", 72, "2026-09-21"),
                ("Cyber Hygiene", "المصادقة الثنائية", 80, "2026-09-28"),
                ("Algorithms", "الترتيب البسيط", 84, "2026-10-05"),
            ],
        ),
        (
            "DS-106",
            "متعلم نموذجي #106",
            "الصف الثالث متوسط",
            [
                ("Algorithms", "تفكيك المشكلات", 88, "2026-09-08"),
                ("Python Logic", "بناء الوحدات البرمجية", 91, "2026-09-15"),
                ("Network Fundamentals", "أمن الشبكات", 86, "2026-09-22"),
                ("Cyber Hygiene", "الخصوصية الرقمية", 94, "2026-09-29"),
                ("Data & Spreadsheets", "تحليل المؤشرات", 90, "2026-10-06"),
            ],
        ),
        (
            "DS-107",
            "متعلم نموذجي #107",
            "الصف الأول متوسط",
            [
                ("Data & Spreadsheets", "الجداول الأساسية", 72, "2026-09-07"),
                ("Network Fundamentals", "مكونات الشبكة", 63, "2026-09-14"),
                ("Python Logic", "الإدخال والإخراج", 60, "2026-09-21"),
                ("Cyber Hygiene", "الأمان على الإنترنت", 77, "2026-09-28"),
                ("Algorithms", "خطوات الحل", 66, "2026-10-05"),
            ],
        ),
        (
            "DS-108",
            "متعلم نموذجي #108",
            "الصف الثاني متوسط",
            [
                ("Python Logic", "الشروط المتداخلة", 44, "2026-09-07"),
                ("Algorithms", "خوارزميات الفرز", 40, "2026-09-14"),
                ("Network Fundamentals", "طبقات الشبكة", 38, "2026-09-21"),
                ("Data & Spreadsheets", "دوال البحث", 52, "2026-09-28"),
                ("Cyber Hygiene", "النسخ الاحتياطي", 61, "2026-10-05"),
            ],
        ),
        (
            "DS-109",
            "متعلم نموذجي #109",
            "الصف الثالث متوسط",
            [
                ("Python Logic", "معالجة النصوص", 83, "2026-09-08"),
                ("Cyber Hygiene", "التشفير الأساسي", 85, "2026-09-15"),
                ("Data & Spreadsheets", "الجداول المحورية", 88, "2026-09-22"),
                ("Algorithms", "التكرار والعودية", 79, "2026-09-29"),
                ("Network Fundamentals", "توجيه البيانات", 82, "2026-10-06"),
            ],
        ),
        (
            "DS-110",
            "متعلم نموذجي #110",
            "الصف الأول متوسط",
            [
                ("Data & Spreadsheets", "تنسيق البيانات", 67, "2026-09-07"),
                ("Cyber Hygiene", "التحقق من الروابط", 71, "2026-09-14"),
                ("Python Logic", "كتابة أول برنامج", 57, "2026-09-21"),
            ],
        ),
        (
            "DS-111",
            "متعلم نموذجي #111",
            "الصف الثاني متوسط",
            [
                ("Network Fundamentals", "بروتوكول DNS", 90, "2026-09-07"),
                ("Python Logic", "تحليل البيانات ببايثون", 92, "2026-09-14"),
                ("Algorithms", "كفاءة الخوارزمية", 89, "2026-09-21"),
                ("Cyber Hygiene", "إدارة الصلاحيات", 95, "2026-09-28"),
                ("Data & Spreadsheets", "النمذجة البيانية", 91, "2026-10-05"),
            ],
        ),
        (
            "DS-112",
            "متعلم نموذجي #112",
            "الصف الثالث متوسط",
            [
                ("Cyber Hygiene", "هندسة اجتماعية", 70, "2026-09-08"),
                ("Network Fundamentals", "أدوات فحص الشبكة", 62, "2026-09-15"),
                ("Python Logic", "الاستثناءات والملفات", 54, "2026-09-22"),
                ("Algorithms", "تتبع التكرار", 51, "2026-09-29"),
                ("Data & Spreadsheets", "ملخصات رقمية", 64, "2026-10-06"),
            ],
        ),
    ]

    records: list[dict[str, Any]] = []
    for student_id, full_name, grade_level, items in cohort_profiles:
        for skill, title, score, assessment_date in items:
            records.append(
                {
                    "student_id": student_id,
                    "full_name": full_name,
                    "grade_level": grade_level,
                    "cohort_term": "Term 2",
                    "skill": skill,
                    "assessment_title": title,
                    "score": score,
                    "max_score": 100,
                    "assessment_date": assessment_date,
                }
            )
    return records


def seed_demo_cohort(db_path: str | Path, force_reset: bool = False) -> dict[str, Any]:
    """Seed an anonymized synthetic Digital Skills demo cohort if empty or reset requested."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        existing_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        if existing_count > 0 and not force_reset:
            return {"seeded": False, "student_count": existing_count}

        if force_reset:
            with conn:
                conn.execute("DELETE FROM interventions")
                conn.execute("DELETE FROM assessments")
                conn.execute("DELETE FROM students")
    finally:
        conn.close()

    ingest_result = ingest_assessment_records(db_path, _build_demo_records())

    record_intervention(
        db_path=db_path,
        student_id="DS-101",
        action_type="1-on-1 Lab Coaching",
        notes="مراجعة تتبع الحلقات التكرارية في بايثون وبناء مخطط تدفق مبسط قبل التقييم القادم.",
        status="IN_PROGRESS",
        teacher_name=DEFAULT_TEACHER_NAME,
    )
    record_intervention(
        db_path=db_path,
        student_id="DS-108",
        action_type="Scaffolded Worksheet",
        notes="تخصيص ورقة عمل تدريجية لمهارتي أساسيات الشبكات والخوارزميات.",
        status="PLANNED",
        teacher_name=DEFAULT_TEACHER_NAME,
    )

    return {
        "seeded": True,
        "student_count": ingest_result["students_affected"],
        "assessment_count": ingest_result["ingested_count"],
    }
