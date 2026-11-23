"""Comprehensive automated test suite for Baseer (بَصِير) Analytics & Diagnostic Platform."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics import (
    calculate_trend_slope,
    classify_risk_tier,
    compute_skill_gaps,
    generate_remediation_plan,
    score_student_risk,
)
from app.main import app
from app.storage import (
    ingest_assessment_records,
    init_db,
    list_interventions,
    parse_csv_records,
    record_intervention,
    seed_demo_cohort,
)


def test_calculate_trend_slope_cases() -> None:
    assert calculate_trend_slope([]) == 0.0
    assert calculate_trend_slope([75.0]) == 0.0
    assert calculate_trend_slope([70.0, 70.0, 70.0]) == 0.0

    improving = calculate_trend_slope([50.0, 60.0, 70.0, 80.0])
    assert improving == pytest.approx(10.0, abs=1e-4)

    declining = calculate_trend_slope([85.0, 75.0, 65.0, 55.0])
    assert declining == pytest.approx(-10.0, abs=1e-4)


def test_explainable_risk_scoring_and_tier_ordering() -> None:
    critical_records = [
        {"skill": "Python Logic", "score": 45, "assessment_date": "2026-09-01"},
        {"skill": "Algorithms", "score": 35, "assessment_date": "2026-09-08"},
        {"skill": "Network Fundamentals", "score": 25, "assessment_date": "2026-09-15"},
    ]
    low_records = [
        {"skill": "Python Logic", "score": 90, "assessment_date": "2026-09-01"},
        {"skill": "Network Fundamentals", "score": 92, "assessment_date": "2026-09-08"},
        {"skill": "Cyber Hygiene", "score": 94, "assessment_date": "2026-09-15"},
        {"skill": "Data & Spreadsheets", "score": 91, "assessment_date": "2026-09-22"},
        {"skill": "Algorithms", "score": 95, "assessment_date": "2026-09-29"},
    ]

    crit_diag = score_student_risk(critical_records)
    low_diag = score_student_risk(low_records)

    assert crit_diag["risk_score"] > low_diag["risk_score"]
    assert crit_diag["risk_tier"] in {"HIGH", "CRITICAL"}
    assert low_diag["risk_tier"] == "LOW"
    assert crit_diag["trend_slope"] < 0
    assert crit_diag["trend_direction"] == "declining"
    assert sum(crit_diag["feature_contributions"].values()) == pytest.approx(
        crit_diag["risk_score"], abs=0.05
    )

    plan = generate_remediation_plan(crit_diag, student_name="DS-999")
    assert plan["priority"] in {"HIGH", "IMMEDIATE"}
    assert len(plan["student_actions"]) >= 1
    assert len(plan["teacher_actions"]) >= 1
    assert classify_risk_tier(75.0) == "CRITICAL"
    assert classify_risk_tier(50.0) == "HIGH"
    assert classify_risk_tier(25.0) == "MEDIUM"
    assert classify_risk_tier(10.0) == "LOW"


def test_compute_skill_gaps_ranking() -> None:
    sample = [
        {"student_id": "S1", "skill": "Python Logic", "score": 40},
        {"student_id": "S2", "skill": "Python Logic", "score": 50},
        {"student_id": "S1", "skill": "Cyber Hygiene", "score": 90},
        {"student_id": "S2", "skill": "Cyber Hygiene", "score": 85},
        {"student_id": "S1", "skill": "Algorithms", "score": 55},
        {"student_id": "S2", "skill": "Algorithms", "score": 78},
    ]
    gaps = compute_skill_gaps(sample)
    assert gaps[0]["skill"] == "Python Logic"
    assert gaps[0]["weakness_rate"] == 100.0
    assert gaps[1]["skill"] == "Algorithms"
    assert gaps[1]["weakness_rate"] == 50.0
    assert gaps[2]["skill"] == "Cyber Hygiene"
    assert gaps[2]["weakness_rate"] == 0.0


def test_csv_parsing_validation_and_sqlite_wal(tmp_path: Path) -> None:
    db_file = tmp_path / "test_wal.db"
    init_db(db_file)

    conn = sqlite3.connect(str(db_file))
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    assert str(mode).lower() == "wal"

    csv_valid = (
        "student_id,full_name,grade_level,skill,assessment_title,score,assessment_date\n"
        "DS-501,متعلم تجريبي 501,الصف الأول متوسط,Python Logic,Loop Quiz,55,2026-10-01\n"
        "DS-501,متعلم تجريبي 501,الصف الأول متوسط,Algorithms,Flowchart Lab,85,2026-10-08\n"
    )
    parsed = parse_csv_records(csv_valid)
    assert len(parsed) == 2
    res = ingest_assessment_records(db_file, parsed)
    assert res == {"ingested_count": 2, "students_affected": 1}

    iv = record_intervention(
        db_path=db_file,
        student_id="DS-501",
        action_type="Scaffolded Worksheet",
        notes="Practice Python loops.",
        status="PLANNED",
    )
    assert iv["student_id"] == "DS-501"
    assert len(list_interventions(db_file, "DS-501")) == 1

    with pytest.raises(ValueError, match="missing required columns"):
        parse_csv_records("student_id,score\nDS-1,80\n")

    with pytest.raises(ValueError, match="out of valid range"):
        parse_csv_records(
            "student_id,skill,score,assessment_date\nDS-1,Python Logic,125,2026-10-01\n"
        )

    with pytest.raises(ValueError, match="Invalid assessment_date"):
        parse_csv_records(
            "student_id,skill,score,assessment_date\nDS-1,Python Logic,75,10/01/2026\n"
        )


def test_fastapi_endpoints_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "api_baseer.db"
    monkeypatch.setenv("BASEER_DB_PATH", str(db_file))
    seed_demo_cohort(db_file, force_reset=True)

    with TestClient(app) as client:
        root_res = client.get("/")
        assert root_res.status_code == 200
        assert "Baseer Edu AI" in root_res.text

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["db_mode"] == "sqlite-wal"
        assert health.json()["student_count"] == 12

        overview = client.get("/api/overview")
        assert overview.status_code == 200
        ov_data = overview.json()
        assert ov_data["kpis"]["total_students"] == 12
        assert len(ov_data["skill_gaps"]) == 5
        scores = [s["risk_score"] for s in ov_data["students"]]
        assert scores == sorted(scores, reverse=True)

        diag_res = client.get("/api/students/DS-101/diagnosis")
        assert diag_res.status_code == 200
        diag_json = diag_res.json()
        assert diag_json["student"]["student_id"] == "DS-101"
        assert diag_json["diagnosis"]["trend_slope"] < 0
        assert "feature_contributions" in diag_json["diagnosis"]
        assert len(diag_json["remediation_plan"]["student_actions"]) >= 1

        not_found = client.get("/api/students/UNKNOWN-999/diagnosis")
        assert not_found.status_code == 404

        json_ingest = client.post(
            "/api/assessments/ingest",
            json={
                "records": [
                    {
                        "student_id": "DS-201",
                        "full_name": "متعلم نموذجي #201",
                        "grade_level": "الصف الثاني متوسط",
                        "skill": "Python Logic",
                        "score": 42,
                        "assessment_date": "2026-10-10",
                    }
                ]
            },
        )
        assert json_ingest.status_code == 200
        assert json_ingest.json()["ingested_count"] == 1

        csv_ingest = client.post(
            "/api/assessments/ingest",
            json={
                "csv_text": (
                    "student_id,full_name,grade_level,skill,score,assessment_date\n"
                    "DS-201,متعلم نموذجي #201,الصف الثاني متوسط,Algorithms,38,2026-10-14\n"
                )
            },
        )
        assert csv_ingest.status_code == 200
        assert csv_ingest.json()["ingested_count"] == 1

        bad_ingest = client.post("/api/assessments/ingest", json={})
        assert bad_ingest.status_code == 422

        iv_res = client.post(
            "/api/interventions",
            json={
                "student_id": "DS-201",
                "action_type": "1-on-1 Lab Coaching",
                "notes": "Targeted Python loop debugging session.",
                "status": "PLANNED",
            },
        )
        assert iv_res.status_code == 201
        assert iv_res.json()["student_id"] == "DS-201"

        iv_missing = client.post(
            "/api/interventions",
            json={
                "student_id": "MISSING-1",
                "action_type": "Coaching",
                "notes": "No such student.",
                "status": "PLANNED",
            },
        )
        assert iv_missing.status_code == 404

        reset_res = client.post("/api/demo/reset")
        assert reset_res.status_code == 200
        assert reset_res.json()["seeded"] is True
        assert reset_res.json()["student_count"] == 12
