"""
Daignose — anonymized data collection for studying racial bias
in human vs. AI medical diagnoses.

Heroku-ready: uses DATABASE_URL (Postgres) if set, falls back to local SQLite.
"""
import csv
import io
import os
import secrets
from datetime import datetime, timezone

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, Response, abort
)
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float, DateTime, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Heroku gives us postgres://... but SQLAlchemy 1.4+ wants postgresql://
db_url = os.environ.get("DATABASE_URL", "sqlite:///daignose.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, pool_pre_ping=True, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False))
Base = declarative_base()

# Optional export password — if EXPORT_PASSWORD env var is set, the export
# and dashboard routes require it as a ?key=... query param.
EXPORT_PASSWORD = os.environ.get("EXPORT_PASSWORD")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class Case(Base):
    """A single anonymized diagnostic case."""
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Submitter context (no PII)
    submitter_role = Column(String(32))   # clinician | researcher | patient | other

    # Patient demographics — anonymized
    patient_race = Column(String(64))
    patient_ethnicity = Column(String(64))
    patient_age_band = Column(String(16))  # 0-9, 10-19, ..., 80+
    patient_sex = Column(String(16))

    # Presentation
    symptoms = Column(Text)

    # Human diagnosis
    human_diagnosis = Column(Text)
    human_confidence = Column(Float)  # 0.0 – 1.0

    # AI diagnosis
    ai_diagnosis = Column(Text)
    ai_confidence = Column(Float)
    ai_model = Column(String(128))

    # Ground truth
    final_diagnosis = Column(Text)
    diagnosis_source = Column(String(64))  # biopsy, follow-up, autopsy, etc.

    notes = Column(Text)


Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Allowed values (kept small, standardized; lets us avoid free-text race fields)
# ---------------------------------------------------------------------------

RACE_OPTIONS = [
    "American Indian or Alaska Native",
    "Asian",
    "Black or African American",
    "Native Hawaiian or Other Pacific Islander",
    "White",
    "Multiracial",
    "Other",
    "Prefer not to say",
]

ETHNICITY_OPTIONS = [
    "Hispanic or Latino",
    "Not Hispanic or Latino",
    "Unknown",
    "Prefer not to say",
]

AGE_BANDS = [
    "0-9", "10-19", "20-29", "30-39", "40-49",
    "50-59", "60-69", "70-79", "80+",
]

SEX_OPTIONS = ["Female", "Male", "Intersex", "Other", "Prefer not to say"]

ROLE_OPTIONS = ["clinician", "researcher", "patient", "other"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_confidence(raw):
    """Accept '0.85', '85', or '' — return float in [0,1] or None."""
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    if val > 1:           # user typed a percent
        val = val / 100.0
    return max(0.0, min(1.0, val))


def _require_key():
    """If EXPORT_PASSWORD is set, require it on protected routes."""
    if EXPORT_PASSWORD and request.args.get("key") != EXPORT_PASSWORD:
        abort(401)


@app.teardown_appcontext
def cleanup(exception=None):
    SessionLocal.remove()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Submission form."""
    with SessionLocal() as s:
        count = s.query(func.count(Case.id)).scalar() or 0
    return render_template(
        "index.html",
        race_options=RACE_OPTIONS,
        ethnicity_options=ETHNICITY_OPTIONS,
        age_bands=AGE_BANDS,
        sex_options=SEX_OPTIONS,
        role_options=ROLE_OPTIONS,
        count=count,
    )


@app.route("/submit", methods=["POST"])
def submit():
    f = request.form

    case = Case(
        submitter_role=f.get("submitter_role") or None,
        patient_race=f.get("patient_race") or None,
        patient_ethnicity=f.get("patient_ethnicity") or None,
        patient_age_band=f.get("patient_age_band") or None,
        patient_sex=f.get("patient_sex") or None,
        symptoms=f.get("symptoms", "").strip() or None,
        human_diagnosis=f.get("human_diagnosis", "").strip() or None,
        human_confidence=_parse_confidence(f.get("human_confidence")),
        ai_diagnosis=f.get("ai_diagnosis", "").strip() or None,
        ai_confidence=_parse_confidence(f.get("ai_confidence")),
        ai_model=f.get("ai_model", "").strip() or None,
        final_diagnosis=f.get("final_diagnosis", "").strip() or None,
        diagnosis_source=f.get("diagnosis_source", "").strip() or None,
        notes=f.get("notes", "").strip() or None,
    )

    with SessionLocal() as s:
        s.add(case)
        s.commit()

    flash("Case recorded. Thank you.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    """Simple aggregate view — no individual records shown."""
    _require_key()
    with SessionLocal() as s:
        total = s.query(func.count(Case.id)).scalar() or 0

        # Per-race rollup: counts + agreement rates
        rows = s.query(Case).all()

    # Compute agreement stats in Python — small N, fine.
    by_race = {}
    for c in rows:
        key = c.patient_race or "Unspecified"
        bucket = by_race.setdefault(key, {
            "n": 0,
            "human_correct": 0, "human_judged": 0,
            "ai_correct": 0, "ai_judged": 0,
        })
        bucket["n"] += 1
        if c.final_diagnosis:
            ft = c.final_diagnosis.strip().lower()
            if c.human_diagnosis:
                bucket["human_judged"] += 1
                if c.human_diagnosis.strip().lower() == ft:
                    bucket["human_correct"] += 1
            if c.ai_diagnosis:
                bucket["ai_judged"] += 1
                if c.ai_diagnosis.strip().lower() == ft:
                    bucket["ai_correct"] += 1

    summary = []
    for race, b in sorted(by_race.items(), key=lambda kv: -kv[1]["n"]):
        h_rate = (b["human_correct"] / b["human_judged"] * 100) if b["human_judged"] else None
        a_rate = (b["ai_correct"] / b["ai_judged"] * 100) if b["ai_judged"] else None
        summary.append({
            "race": race, "n": b["n"],
            "human_accuracy": h_rate,
            "ai_accuracy": a_rate,
            "human_n": b["human_judged"],
            "ai_n": b["ai_judged"],
        })

    return render_template(
        "dashboard.html",
        total=total,
        summary=summary,
        key=request.args.get("key", ""),
    )


@app.route("/export.csv")
def export_csv():
    """Full anonymized dataset as CSV."""
    _require_key()

    with SessionLocal() as s:
        cases = s.query(Case).order_by(Case.id.asc()).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "created_at_utc", "submitter_role",
        "patient_race", "patient_ethnicity", "patient_age_band", "patient_sex",
        "symptoms",
        "human_diagnosis", "human_confidence",
        "ai_diagnosis", "ai_confidence", "ai_model",
        "final_diagnosis", "diagnosis_source",
        "human_matches_final", "ai_matches_final",
        "notes",
    ])
    for c in cases:
        ft = (c.final_diagnosis or "").strip().lower()
        h_match = (c.human_diagnosis.strip().lower() == ft) if (c.human_diagnosis and ft) else ""
        a_match = (c.ai_diagnosis.strip().lower() == ft) if (c.ai_diagnosis and ft) else ""
        w.writerow([
            c.id,
            c.created_at.isoformat() if c.created_at else "",
            c.submitter_role or "",
            c.patient_race or "", c.patient_ethnicity or "",
            c.patient_age_band or "", c.patient_sex or "",
            c.symptoms or "",
            c.human_diagnosis or "",
            "" if c.human_confidence is None else f"{c.human_confidence:.3f}",
            c.ai_diagnosis or "",
            "" if c.ai_confidence is None else f"{c.ai_confidence:.3f}",
            c.ai_model or "",
            c.final_diagnosis or "", c.diagnosis_source or "",
            h_match, a_match,
            c.notes or "",
        ])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="daignose-export-{datetime.utcnow():%Y%m%d-%H%M%S}.csv"',
        },
    )


@app.route("/healthz")
def healthz():
    return {"ok": True, "cases": SessionLocal().query(func.count(Case.id)).scalar() or 0}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
