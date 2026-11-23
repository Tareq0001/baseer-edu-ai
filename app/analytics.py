"""Pure-Python learning analytics, linear trend regression, and explainable risk engine."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

CURRICULUM_SKILLS: tuple[str, ...] = (
    "Python Logic",
    "Network Fundamentals",
    "Cyber Hygiene",
    "Data & Spreadsheets",
    "Algorithms",
)

SKILL_METADATA: dict[str, dict[str, str]] = {
    "Python Logic": {
        "ar": "منطق بايثون والبرمجة",
        "step_ar": "تطبيق تمارين متدرجة على المتغيرات والحلقات والشروط في محاكي بايثون مع تتبع المخرجات سطراً بسطر.",
        "step_en": "Complete scaffolded Python loop and conditional tracing exercises with step-by-step output verification.",
    },
    "Network Fundamentals": {
        "ar": "أساسيات الشبكات والبروتوكولات",
        "step_ar": "مراجعة بنية الحزم وعناوين IP وبروتوكولات TCP/HTTP عبر محاكي توجيه الشبكة.",
        "step_en": "Review packet flow, IP addressing, and TCP/HTTP layers using interactive network routing scenarios.",
    },
    "Cyber Hygiene": {
        "ar": "الأمن السيبراني والوعي الرقمي",
        "step_ar": "حل سيناريوهات كشف التصيد الإلكتروني وإعداد كلمات المرور القوية والمصادقة الثنائية.",
        "step_en": "Practice phishing triage scenarios, strong password hygiene, and multi-factor authentication checks.",
    },
    "Data & Spreadsheets": {
        "ar": "تحليل البيانات والجداول الإلكترونية",
        "step_ar": "بناء جدول تطبيقي باستخدام دوال SUM و AVERAGE و IF مع التحقق من صحة البيانات.",
        "step_en": "Build a guided spreadsheet lab using SUM, AVERAGE, IF formulas, and conditional data validation.",
    },
    "Algorithms": {
        "ar": "الخوارزميات وحل المشكلات",
        "step_ar": "تفكيك المسائل إلى خطوات منطقية ومقارنة البحث الخطي والثنائي بمخططات التدفق.",
        "step_en": "Decompose problems into flowcharts and trace linear vs. binary search step counts.",
    },
}

DEFAULT_WEAKNESS_THRESHOLD = 65.0
DEFAULT_EXPECTED_ASSESSMENTS = 5


def _normalize_score(record: dict[str, Any]) -> float:
    """Normalize an assessment record's score to a 0-100 percentage."""
    raw_score = float(record.get("score", 0.0))
    max_score = float(record.get("max_score", 100.0) or 100.0)
    if max_score <= 0:
        raise ValueError("max_score must be positive")
    pct = (raw_score / max_score) * 100.0
    return round(min(100.0, max(0.0, pct)), 2)


def calculate_trend_slope(scores: Sequence[float]) -> float:
    """Compute ordinary least squares (OLS) linear regression slope across ordered scores.

    Returns 0.0 when fewer than 2 scores are provided.
    Positive values indicate an improving trajectory; negative values indicate decline.
    """
    n = len(scores)
    if n < 2:
        return 0.0

    mean_x = (n - 1) / 2.0
    mean_y = sum(float(s) for s in scores) / n
    numerator = sum((i - mean_x) * (float(s) - mean_y) for i, s in enumerate(scores))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0.0:
        return 0.0
    return round(numerator / denominator, 4)


def classify_risk_tier(risk_score: float) -> str:
    """Map numeric risk score (0-100) to deterministic risk tier."""
    if risk_score >= 75.0:
        return "CRITICAL"
    if risk_score >= 50.0:
        return "HIGH"
    if risk_score >= 25.0:
        return "MEDIUM"
    return "LOW"


def score_student_risk(
    assessments: Sequence[dict[str, Any]],
    expected_assessments: int = DEFAULT_EXPECTED_ASSESSMENTS,
) -> dict[str, Any]:
    """Calculate an explainable 0-100 risk score with exact feature contributions.

    Components (sum up to 100):
    - mastery_deficit (0-45): (100 - mastery_avg) * 0.45
    - negative_trajectory (0-25): min(25, abs(slope) * 1.5) when slope < 0
    - completion_gap (0-15): (1 - min(1, completed / expected)) * 15
    - skill_concentration_gap (0-15): min(15, max(0, 70 - weakest_skill_avg) * 0.3)
    """
    ordered = sorted(
        assessments,
        key=lambda item: (str(item.get("assessment_date", "")), int(item.get("id", 0) or 0)),
    )

    if not ordered:
        contributions = {
            "mastery_deficit": 45.0,
            "negative_trajectory": 0.0,
            "completion_gap": 15.0,
            "skill_concentration_gap": 15.0,
        }
        total = round(sum(contributions.values()), 2)
        return {
            "mastery_avg": 0.0,
            "trend_slope": 0.0,
            "trend_direction": "stable",
            "completion_rate": 0.0,
            "assessment_count": 0,
            "risk_score": total,
            "risk_tier": classify_risk_tier(total),
            "weak_skills": list(CURRICULUM_SKILLS),
            "skill_breakdown": {},
            "feature_contributions": contributions,
            "reasons_en": ["No assessment submissions recorded yet."],
            "reasons_ar": ["لم تُسجّل أي تقييمات للطالب حتى الآن."],
        }

    normalized_scores = [_normalize_score(r) for r in ordered]
    mastery_avg = round(sum(normalized_scores) / len(normalized_scores), 2)
    trend_slope = calculate_trend_slope(normalized_scores)

    if trend_slope > 1.5:
        trend_direction = "improving"
    elif trend_slope < -1.5:
        trend_direction = "declining"
    else:
        trend_direction = "stable"

    target_count = max(1, int(expected_assessments))
    completion_ratio = min(1.0, len(ordered) / target_count)
    completion_rate = round(completion_ratio * 100.0, 2)

    skill_buckets: dict[str, list[float]] = defaultdict(list)
    for rec, norm in zip(ordered, normalized_scores):
        skill_name = str(rec.get("skill") or "General")
        skill_buckets[skill_name].append(norm)

    skill_breakdown = {
        skill: round(sum(vals) / len(vals), 2)
        for skill, vals in skill_buckets.items()
    }
    weak_skills = [
        skill
        for skill, avg in sorted(skill_breakdown.items(), key=lambda kv: kv[1])
        if avg < DEFAULT_WEAKNESS_THRESHOLD
    ]
    weakest_skill_avg = min(skill_breakdown.values())

    mastery_deficit = round(max(0.0, min(45.0, (100.0 - mastery_avg) * 0.45)), 2)
    negative_trajectory = (
        round(min(25.0, abs(trend_slope) * 1.5), 2) if trend_slope < 0 else 0.0
    )
    completion_gap = round(max(0.0, (1.0 - completion_ratio) * 15.0), 2)
    skill_concentration_gap = (
        round(min(15.0, (70.0 - weakest_skill_avg) * 0.3), 2)
        if weakest_skill_avg < 70.0
        else 0.0
    )

    contributions = {
        "mastery_deficit": mastery_deficit,
        "negative_trajectory": negative_trajectory,
        "completion_gap": completion_gap,
        "skill_concentration_gap": skill_concentration_gap,
    }
    risk_score = round(min(100.0, max(0.0, sum(contributions.values()))), 2)
    risk_tier = classify_risk_tier(risk_score)

    reasons_en: list[str] = []
    reasons_ar: list[str] = []
    if mastery_avg < 70.0:
        reasons_en.append(f"Mastery average is {mastery_avg:.1f}% (target >= 70%).")
        reasons_ar.append(f"متوسط الإتقان {mastery_avg:.1f}% أقل من الحد المستهدف (70%).")
    if trend_slope < -1.5:
        reasons_en.append(f"Declining score trajectory (slope {trend_slope:+.2f} pts/assessment).")
        reasons_ar.append(f"مسار الدرجات متراجع (الميل {trend_slope:+.2f} نقطة لكل تقييم).")
    elif trend_slope > 1.5:
        reasons_en.append(f"Improving score trajectory (slope {trend_slope:+.2f} pts/assessment).")
        reasons_ar.append(f"مسار الدرجات متصاعد (الميل {trend_slope:+.2f} نقطة لكل تقييم).")
    if completion_rate < 100.0:
        reasons_en.append(f"Completed {len(ordered)} of {target_count} expected assessments ({completion_rate:.0f}%).")
        reasons_ar.append(f"أنجز {len(ordered)} من أصل {target_count} تقييمات متوقعة ({completion_rate:.0f}%).")
    if weak_skills:
        joined = ", ".join(weak_skills)
        reasons_en.append(f"Weak curriculum skill concentration in: {joined}.")
        joined_ar = "، ".join(SKILL_METADATA.get(s, {}).get("ar", s) for s in weak_skills)
        reasons_ar.append(f"تركّز الضعف المهاري في: {joined_ar}.")
    if not reasons_en:
        reasons_en.append("Consistent mastery across all evaluated Digital Skills.")
        reasons_ar.append("إتقان مستقر ومتوازن في جميع مهارات المنهج المقيّمة.")

    return {
        "mastery_avg": mastery_avg,
        "trend_slope": trend_slope,
        "trend_direction": trend_direction,
        "completion_rate": completion_rate,
        "assessment_count": len(ordered),
        "risk_score": risk_score,
        "risk_tier": risk_tier,
        "weak_skills": weak_skills,
        "skill_breakdown": skill_breakdown,
        "feature_contributions": contributions,
        "reasons_en": reasons_en,
        "reasons_ar": reasons_ar,
    }


def compute_skill_gaps(
    assessments: Sequence[dict[str, Any]],
    weakness_threshold: float = DEFAULT_WEAKNESS_THRESHOLD,
) -> list[dict[str, Any]]:
    """Rank curriculum skills by cohort weakness/failure rate descending."""
    grouped: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for rec in assessments:
        skill = str(rec.get("skill") or "").strip()
        if not skill:
            continue
        student_id = str(rec.get("student_id") or "")
        grouped[skill].append((student_id, _normalize_score(rec)))

    all_skills = list(CURRICULUM_SKILLS) + [
        s for s in sorted(grouped.keys()) if s not in CURRICULUM_SKILLS
    ]

    gaps: list[dict[str, Any]] = []
    for skill in all_skills:
        entries = grouped.get(skill, [])
        if not entries:
            continue
        scores = [s for _, s in entries]
        student_ids = {sid for sid, _ in entries if sid}
        weak_count = sum(1 for s in scores if s < weakness_threshold)
        weakness_rate = round((weak_count / len(scores)) * 100.0, 2)
        avg_score = round(sum(scores) / len(scores), 2)

        if weakness_rate >= 50.0:
            severity = "CRITICAL"
        elif weakness_rate >= 30.0:
            severity = "HIGH"
        elif weakness_rate >= 15.0:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        gaps.append(
            {
                "skill": skill,
                "skill_ar": SKILL_METADATA.get(skill, {}).get("ar", skill),
                "attempt_count": len(scores),
                "student_count": len(student_ids),
                "avg_score": avg_score,
                "weak_count": weak_count,
                "weakness_rate": weakness_rate,
                "severity": severity,
            }
        )

    gaps.sort(key=lambda g: (-g["weakness_rate"], g["avg_score"], g["skill"]))
    return gaps


def generate_remediation_plan(
    diagnosis: dict[str, Any],
    student_name: str = "",
) -> dict[str, Any]:
    """Produce actionable bilingual (AR/EN) study steps and teacher intervention actions."""
    tier = str(diagnosis.get("risk_tier", "LOW"))
    trend = str(diagnosis.get("trend_direction", "stable"))
    weak_skills: list[str] = list(diagnosis.get("weak_skills") or [])
    breakdown: dict[str, float] = dict(diagnosis.get("skill_breakdown") or {})
    completion_rate = float(diagnosis.get("completion_rate", 100.0))

    priority_map = {
        "CRITICAL": ("IMMEDIATE", "عاجل (خلال 48 ساعة)"),
        "HIGH": ("HIGH", "أولوية عالية (هذا الأسبوع)"),
        "MEDIUM": ("MODERATE", "متابعة وقائية"),
        "LOW": ("ENRICHMENT", "إثراء وتمكين"),
    }
    priority, priority_ar = priority_map.get(tier, ("MODERATE", "متابعة"))

    label = student_name or "Student"
    if tier in ("CRITICAL", "HIGH"):
        summary_en = (
            f"{label} requires targeted scaffolded support ({tier} risk, trend: {trend}). "
            "Focus on core skill bottlenecks before the next summative checkpoint."
        )
        summary_ar = (
            f"يحتاج المتعلم ({label}) إلى خطة دعم علاجي مركّزة (مستوى الخطر: {tier}، الاتجاه: {trend}) "
            "لمعالجة الفجوات المهارية قبل التقييم الختامي القادم."
        )
    elif tier == "MEDIUM":
        summary_en = (
            f"{label} shows moderate risk signals; targeted skill reinforcement will stabilize mastery."
        )
        summary_ar = (
            f"تظهر لدى المتعلم ({label}) مؤشرات خطر متوسطة؛ يكفي تعزيز المهارات الأضعف لتثبيت الإتقان."
        )
    else:
        summary_en = (
            f"{label} demonstrates strong mastery. Assign challenge labs and peer mentoring tasks."
        )
        summary_ar = (
            f"يُظهر المتعلم ({label}) إتقاناً قوياً؛ يُنصح بتكليفه بتحديات إثرائية وتدريب الأقران."
        )

    target_skills = weak_skills if weak_skills else (
        [min(breakdown, key=breakdown.get)] if breakdown else ["Python Logic"]
    )

    student_actions: list[dict[str, Any]] = []
    for skill in target_skills[:3]:
        meta = SKILL_METADATA.get(skill, {})
        current_avg = breakdown.get(skill, 0.0)
        student_actions.append(
            {
                "skill": skill,
                "skill_ar": meta.get("ar", skill),
                "current_avg": current_avg,
                "target_score": max(75.0, min(95.0, round(current_avg + 20.0, 1))),
                "step_ar": meta.get(
                    "step_ar",
                    f"مراجعة بطاقات المفهوم وحل تدريب تفاعلي قصير في مهارة {skill}.",
                ),
                "step_en": meta.get(
                    "step_en",
                    f"Review concept cards and complete a targeted practice check in {skill}.",
                ),
            }
        )

    teacher_actions: list[dict[str, str]] = []
    if tier in ("CRITICAL", "HIGH"):
        teacher_actions.append(
            {
                "urgency": "IMMEDIATE",
                "action_ar": "جدولة جلسة دعم فردية مدتها 15 دقيقة في المعمل لمراجعة الأخطاء المفاهيمية.",
                "action_en": "Schedule a 15-minute 1-on-1 lab coaching session to diagnose misconceptions.",
            }
        )
    if completion_rate < 80.0:
        teacher_actions.append(
            {
                "urgency": "HIGH",
                "action_ar": "متابعة تسليم المهام المفقودة وفتح نافذة تعويضية موجهة.",
                "action_en": "Follow up on missing assessments and open a structured make-up window.",
            }
        )
    if trend == "declining":
        teacher_actions.append(
            {
                "urgency": "HIGH",
                "action_ar": "تقسيم التكليف القادم إلى خطوات قصيرة مع تحقق مرحلي في منتصف الحصة.",
                "action_en": "Scaffold the next lab into micro-checkpoints with mid-class feedback.",
            }
        )
    if not teacher_actions:
        teacher_actions.append(
            {
                "urgency": "ROUTINE",
                "action_ar": "تقديم مسألة إثرائية متقدمة وإشراك المتعلم في دعم زملائه أثناء التطبيق العملي.",
                "action_en": "Assign an enrichment challenge and pair the student as a lab peer-mentor.",
            }
        )

    return {
        "priority": priority,
        "priority_ar": priority_ar,
        "summary_ar": summary_ar,
        "summary_en": summary_en,
        "student_actions": student_actions,
        "teacher_actions": teacher_actions,
    }
