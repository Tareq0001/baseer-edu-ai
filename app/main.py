"""FastAPI application for Baseer (بَصِير) Student Performance Analytics & Diagnostic Platform."""

from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.analytics import (
    compute_skill_gaps,
    generate_remediation_plan,
    score_student_risk,
)
from app.storage import (
    DEFAULT_TEACHER_NAME,
    get_student,
    get_student_assessments,
    ingest_assessment_records,
    init_db,
    list_all_assessments,
    list_all_students,
    list_interventions,
    parse_csv_records,
    record_intervention,
    seed_demo_cohort,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_INDEX_PATH = PROJECT_ROOT / "docs" / "index.html"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "baseer.db"


def get_db_path() -> Path:
    """Resolve active SQLite database path from environment or project default."""
    return Path(os.environ.get("BASEER_DB_PATH", str(DEFAULT_DB_PATH)))


class AssessmentRecordIn(BaseModel):
    student_id: str = Field(..., min_length=1, max_length=64)
    full_name: str | None = Field(default=None, max_length=120)
    grade_level: str | None = Field(default="الصف الأول متوسط", max_length=80)
    cohort_term: str | None = Field(default="Term 2", max_length=40)
    assessment_title: str | None = Field(default=None, max_length=160)
    skill: str = Field(..., min_length=1, max_length=80)
    score: float = Field(..., ge=0)
    max_score: float = Field(default=100.0, gt=0)
    assessment_date: str = Field(..., min_length=10, max_length=10)


class IngestPayload(BaseModel):
    records: list[AssessmentRecordIn] | None = None
    csv_text: str | None = None


class InterventionCreate(BaseModel):
    student_id: str = Field(..., min_length=1, max_length=64)
    action_type: str = Field(..., min_length=2, max_length=120)
    notes: str = Field(..., min_length=3, max_length=1000)
    status: str = Field(default="PLANNED", max_length=32)
    teacher_name: str = Field(default=DEFAULT_TEACHER_NAME, max_length=120)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db_path = get_db_path()
    init_db(db_path)
    seed_demo_cohort(db_path, force_reset=False)
    yield


app = FastAPI(
    title="Baseer (بَصِير) — AI Student Performance Analytics & Diagnostic Platform",
    version="1.0.0",
    lifespan=lifespan,
)


def _ensure_initialized(db_path: Path) -> None:
    init_db(db_path)


@app.get("/", include_in_schema=False)
def serve_dashboard() -> FileResponse:
    if not DOCS_INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="Frontend dashboard not found")
    return FileResponse(DOCS_INDEX_PATH, media_type="text/html; charset=utf-8")


@app.get("/health")
def health_check() -> dict[str, Any]:
    db_path = get_db_path()
    _ensure_initialized(db_path)
    students = list_all_students(db_path)
    assessments = list_all_assessments(db_path)
    return {
        "status": "ok",
        "service": "baseer-edu-ai",
        "db_mode": "sqlite-wal",
        "student_count": len(students),
        "assessment_count": len(assessments),
    }


@app.get("/api/overview")
def get_cohort_overview() -> dict[str, Any]:
    db_path = get_db_path()
    _ensure_initialized(db_path)
    students = list_all_students(db_path)
    assessments = list_all_assessments(db_path)
    interventions = list_interventions(db_path, limit=25)

    by_student: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in assessments:
        by_student[rec["student_id"]].append(rec)

    risk_distribution = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    roster: list[dict[str, Any]] = []
    mastery_values: list[float] = []

    for stu in students:
        stu_records = by_student.get(stu["student_id"], [])
        diag = score_student_risk(stu_records)
        tier = diag["risk_tier"]
        risk_distribution[tier] = risk_distribution.get(tier, 0) + 1
        if diag["assessment_count"] > 0:
            mastery_values.append(diag["mastery_avg"])

        roster.append(
            {
                "student_id": stu["student_id"],
                "full_name": stu["full_name"],
                "grade_level": stu["grade_level"],
                "cohort_term": stu["cohort_term"],
                "assessment_count": diag["assessment_count"],
                "mastery_avg": diag["mastery_avg"],
                "trend_slope": diag["trend_slope"],
                "trend_direction": diag["trend_direction"],
                "completion_rate": diag["completion_rate"],
                "risk_score": diag["risk_score"],
                "risk_tier": tier,
                "weak_skills": diag["weak_skills"],
                "feature_contributions": diag["feature_contributions"],
            }
        )

    roster.sort(key=lambda r: (-r["risk_score"], r["mastery_avg"], r["student_id"]))
    skill_gaps = compute_skill_gaps(assessments)
    avg_mastery = (
        round(sum(mastery_values) / len(mastery_values), 2) if mastery_values else 0.0
    )
    open_interventions = sum(
        1 for iv in interventions if iv.get("status") != "COMPLETED"
    )

    return {
        "kpis": {
            "total_students": len(students),
            "total_assessments": len(assessments),
            "avg_mastery": avg_mastery,
            "at_risk_count": risk_distribution["CRITICAL"] + risk_distribution["HIGH"],
            "critical_count": risk_distribution["CRITICAL"],
            "open_interventions": open_interventions,
        },
        "risk_distribution": risk_distribution,
        "skill_gaps": skill_gaps,
        "students": roster,
        "recent_interventions": interventions,
    }


@app.post("/api/assessments/ingest")
def ingest_assessments(payload: IngestPayload) -> dict[str, Any]:
    db_path = get_db_path()
    _ensure_initialized(db_path)

    has_records = payload.records is not None and len(payload.records) > 0
    has_csv = bool(payload.csv_text and payload.csv_text.strip())
    if not has_records and not has_csv:
        raise HTTPException(
            status_code=422,
            detail="Provide either non-empty 'records' list or 'csv_text'",
        )

    try:
        if has_csv:
            records_to_ingest = parse_csv_records(payload.csv_text or "")
        else:
            records_to_ingest = [r.model_dump() for r in (payload.records or [])]
        result = ingest_assessment_records(db_path, records_to_ingest)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"status": "ingested", **result}


@app.get("/api/students/{student_id}/diagnosis")
def get_student_diagnosis(student_id: str) -> dict[str, Any]:
    db_path = get_db_path()
    _ensure_initialized(db_path)

    student = get_student(db_path, student_id)
    if not student:
        raise HTTPException(status_code=404, detail=f"Student '{student_id}' not found")

    assessments = get_student_assessments(db_path, student_id)
    diagnosis = score_student_risk(assessments)
    remediation_plan = generate_remediation_plan(
        diagnosis, student_name=student["full_name"]
    )
    interventions = list_interventions(db_path, student_id=student_id, limit=20)

    return {
        "student": student,
        "assessments": assessments,
        "diagnosis": diagnosis,
        "remediation_plan": remediation_plan,
        "interventions": interventions,
    }


@app.post("/api/interventions", status_code=201)
def create_intervention(payload: InterventionCreate) -> JSONResponse:
    db_path = get_db_path()
    _ensure_initialized(db_path)

    try:
        created = record_intervention(
            db_path=db_path,
            student_id=payload.student_id,
            action_type=payload.action_type,
            notes=payload.notes,
            status=payload.status,
            teacher_name=payload.teacher_name,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return JSONResponse(status_code=201, content=created)


@app.post("/api/demo/reset")
def reset_demo() -> dict[str, Any]:
    db_path = get_db_path()
    return seed_demo_cohort(db_path, force_reset=True)
