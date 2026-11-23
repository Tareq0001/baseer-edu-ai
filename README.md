# Baseer (`بَصِير`) — AI Student Performance Analytics & Diagnostic Platform

**Baseer (`بَصِير`)** is an explainable student performance analytics, early-warning risk scoring, and adaptive remediation platform engineered by **Tareq Abuashi (أ. طارق ابوعشي)**. It bridges real Digital Skills middle-school instruction at Abha National Schools (`متوسطة أبها الأهلية`) with production Python, FastAPI, SQL, and learning-analytics engineering.

Live Browser Analytics Lab: https://tareq0001.github.io/baseer-edu-ai/

---

## Architecture Overview

```text
+-------------------------------------------------------------------+
|                   Dual-Mode Frontend (docs/index.html)            |
|  - Academic Slate-Teal UI | AR/EN (RTL/LTR) | Dark/Light Toggle   |
|  - Live FastAPI Mode  OR  Interactive Browser Analytics Lab       |
+---------------------------------+---------------------------------+
                                  | REST JSON (/health, /api/*)
                                  v
+-------------------------------------------------------------------+
|                 FastAPI Service Layer (app/main.py)               |
|  - Pydantic v2 request validation & static dashboard serving      |
|  - Endpoints: /overview, /assessments/ingest, /diagnosis,         |
|               /interventions, /demo/reset                         |
+----------------+--------------------------------+-----------------+
                 |                                |
                 v                                v
+-------------------------------+  +--------------------------------+
| Explainable Analytics Engine  |  | Persistence & ETL Layer        |
| (app/analytics.py)            |  | (app/storage.py + sql/)        |
| - OLS linear trend slope      |  | - SQLite WAL + FK constraints  |
| - 4-factor risk score (0-100) |  | - PostgreSQL 16 analytics DDL  |
| - Cohort skill-gap matrix     |  | - Transactional CSV/JSON ETL   |
| - Bilingual remediation plan  |  | - Anonymized demo seeder       |
+-------------------------------+  +--------------------------------+
```

---

## Core Engineering Features

1. **OLS Linear Trend Regression (`calculate_trend_slope`)**
   Computes the exact ordinary least squares slope across chronologically ordered assessments to distinguish improving (`> +1.5`), stable, and declining (`< -1.5`) student trajectories.
2. **Deterministic Explainable Risk Engine (`score_student_risk`)**
   Produces a `0–100` risk score mapped to `LOW`, `MEDIUM`, `HIGH`, and `CRITICAL` tiers with transparent `feature_contributions`:
   - `mastery_deficit` (`0–45 pts`): `(100 - mastery_avg) * 0.45`
   - `negative_trajectory` (`0–25 pts`): `min(25, abs(slope) * 1.5)` when `slope < 0`
   - `completion_gap` (`0–15 pts`): `(1 - completion_ratio) * 15`
   - `skill_concentration_gap` (`0–15 pts`): `min(15, (70 - weakest_skill_avg) * 0.3)`
3. **Cohort Skill-Gap Matrix (`compute_skill_gaps`)**
   Aggregates Digital Skills (`Python Logic`, `Network Fundamentals`, `Cyber Hygiene`, `Data & Spreadsheets`, `Algorithms`) and ranks curriculum bottlenecks by cohort weakness rate (`score < 65%`).
4. **Adaptive Bilingual Remediation (`generate_remediation_plan`)**
   Generates tailored Arabic/English student practice steps and teacher intervention actions.
5. **SQLite WAL + PostgreSQL 16 Production Schema**
   Uses parameterized SQL queries, composite indexes (`(student_id, assessment_date)`, `(skill)`, `(student_id, status)`), and analytical SQL views (`sql/init_postgres.sql`).

---

## Quickstart

### 1. Run Locally with FastAPI & Uvicorn

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` in your browser. The dashboard automatically detects `/health` and activates **Live FastAPI Mode**.

### 2. Run with Docker Compose (FastAPI + PostgreSQL 16)

```bash
docker compose up --build
```

---

## API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the bilingual `docs/index.html` diagnostic studio |
| `GET` | `/health` | Service health check, SQLite WAL status, and record counts |
| `GET` | `/api/overview` | Cohort KPIs, risk distribution, skill-gap matrix, sorted student roster |
| `POST` | `/api/assessments/ingest` | Validates and ingests JSON `records` or raw `csv_text` in one transaction |
| `GET` | `/api/students/{student_id}/diagnosis` | Full explainable risk diagnosis, OLS slope, remediation plan, and interventions |
| `POST` | `/api/interventions` | Records a teacher intervention action (`PLANNED`, `IN_PROGRESS`, `COMPLETED`) |
| `POST` | `/api/demo/reset` | Resets and re-seeds the anonymized synthetic Digital Skills cohort |

---

## Automated Tests

```bash
python -m pytest -v
```

Verified output:

```text
tests/test_baseer.py::test_calculate_trend_slope_cases PASSED            [ 20%]
tests/test_baseer.py::test_explainable_risk_scoring_and_tier_ordering PASSED [ 40%]
tests/test_baseer.py::test_compute_skill_gaps_ranking PASSED             [ 60%]
tests/test_baseer.py::test_csv_parsing_validation_and_sqlite_wal PASSED  [ 80%]
tests/test_baseer.py::test_fastapi_endpoints_end_to_end PASSED           [100%]

5 passed in 0.56s
```
