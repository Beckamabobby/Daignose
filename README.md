# Daignose

A minimal, anonymized data-collection webapp for studying racial bias in
medical diagnoses — specifically, comparing human vs. AI diagnostic accuracy
across patient demographics.

## What it captures

Per case:

- **Submitter context** — role only (clinician / researcher / patient / other)
- **Patient demographics** — race, ethnicity, age band (10-year), sex. No DOB, no names, no MRN.
- **Symptoms presented** — free text
- **Human diagnosis** + confidence (0–1 or 0–100)
- **AI diagnosis** + confidence + model/version string
- **Final confirmed diagnosis** + how it was confirmed (biopsy, follow-up, etc.)
- **Notes** — optional

## What it does NOT capture

No names, dates of birth, addresses, MRNs, IPs, or any other identifiers.
Cases are stored with an auto-incrementing ID and a UTC timestamp only.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /` | Submission form |
| `POST /submit` | Record a case |
| `GET /dashboard` | Aggregate accuracy by patient race |
| `GET /export.csv` | Full anonymized dataset as CSV |
| `GET /healthz` | Health check |

If you set `EXPORT_PASSWORD`, the dashboard and export require `?key=...`.

## Run locally

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

Data lands in `daignose.db` (SQLite) by default.

## Deploy to Heroku

```bash
heroku create daignose
heroku addons:create heroku-postgresql:essential-0
heroku config:set SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))')
heroku config:set EXPORT_PASSWORD=pick-a-strong-one      # optional but recommended
git push heroku main
```

Heroku injects `DATABASE_URL` automatically — the app rewrites the old
`postgres://` scheme to `postgresql://` for SQLAlchemy 2.x.

## Analysis

The CSV includes pre-computed `human_matches_final` and `ai_matches_final`
booleans alongside the raw diagnosis strings, so you can pull it into pandas,
R, or SPSS and immediately stratify by `patient_race`, `patient_age_band`,
`patient_sex`, and `ai_model`.

Exact-match accuracy is coarse on its own — for real work you'll want to
normalize diagnosis strings (ICD-10 mapping, lowercase, alias resolution)
before computing rates.

## Ethical & legal notes

This tool is a *research instrument*, not a clinical system. Before deploying
in any real setting:

- Get IRB / ethics approval. "Anonymized" is a strong claim — symptom
  narratives plus rare diagnoses can re-identify in small populations.
- Decide who can submit and who can export. The `EXPORT_PASSWORD` gate is
  the minimum; consider proper auth (Flask-Login, OAuth, SSO) for real use.
- Don't paste anything from a real chart into the symptoms field without
  your institution's de-identification policy applied first.
