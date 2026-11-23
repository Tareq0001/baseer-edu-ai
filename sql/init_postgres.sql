-- Baseer (بَصِير) PostgreSQL 16 Analytics DDL & Cohort Views

CREATE TABLE IF NOT EXISTS students (
    student_id VARCHAR(64) PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    grade_level VARCHAR(80) NOT NULL,
    cohort_term VARCHAR(40) NOT NULL DEFAULT 'Term 2',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assessments (
    id BIGSERIAL PRIMARY KEY,
    student_id VARCHAR(64) NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    assessment_title VARCHAR(160) NOT NULL,
    skill VARCHAR(80) NOT NULL,
    score NUMERIC(5, 2) NOT NULL CHECK (score >= 0 AND score <= 100),
    max_score NUMERIC(6, 2) NOT NULL DEFAULT 100.00 CHECK (max_score > 0),
    assessment_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS interventions (
    id BIGSERIAL PRIMARY KEY,
    student_id VARCHAR(64) NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    teacher_name VARCHAR(120) NOT NULL,
    action_type VARCHAR(120) NOT NULL,
    notes TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PLANNED'
        CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessments_student_date
    ON assessments (student_id, assessment_date ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_assessments_skill
    ON assessments (skill);

CREATE INDEX IF NOT EXISTS idx_interventions_student_status
    ON interventions (student_id, status);

CREATE OR REPLACE VIEW v_cohort_skill_summary AS
SELECT
    skill,
    COUNT(*) AS attempt_count,
    COUNT(DISTINCT student_id) AS student_count,
    ROUND(AVG(score), 2) AS avg_score,
    SUM(CASE WHEN score < 65 THEN 1 ELSE 0 END) AS weak_count,
    ROUND(
        (SUM(CASE WHEN score < 65 THEN 1 ELSE 0 END)::NUMERIC / NULLIF(COUNT(*), 0)) * 100,
        2
    ) AS weakness_rate
FROM assessments
GROUP BY skill
ORDER BY weakness_rate DESC, avg_score ASC;

CREATE OR REPLACE VIEW v_student_cohort_summary AS
SELECT
    s.student_id,
    s.full_name,
    s.grade_level,
    s.cohort_term,
    COUNT(a.id) AS assessment_count,
    ROUND(COALESCE(AVG(a.score), 0), 2) AS avg_score,
    MIN(a.assessment_date) AS first_assessment_date,
    MAX(a.assessment_date) AS last_assessment_date,
    COUNT(DISTINCT i.id) FILTER (WHERE i.status <> 'COMPLETED') AS open_interventions
FROM students s
LEFT JOIN assessments a ON a.student_id = s.student_id
LEFT JOIN interventions i ON i.student_id = s.student_id
GROUP BY s.student_id, s.full_name, s.grade_level, s.cohort_term;
