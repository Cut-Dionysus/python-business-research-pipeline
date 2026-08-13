from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_CITIES = {"Huntsville", "Arab", "Guntersville"}
ELIGIBLE_INDUSTRIES = {"medical", "manufacturing", "engineering", "church", "school", "property management", "bank", "automotive", "veterinary"}
BUYING_SIGNALS = {
    "new_location": ("new location", "new office", "grand opening"),
    "growth": ("expanding", "expansion", "growing"),
    "hiring": ("now hiring", "hiring"),
    "facility": ("facility", "headquarters", "campus"),
}
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

@dataclass(frozen=True)
class Lead:
    company: str
    city: str
    industry: str
    website: str
    score: int
    score_reasons: list[str]

def load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

def normalize_company(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())

def valid_website(value: str) -> bool:
    if not URL_RE.match(value):
        return False
    parsed = urlparse(value)
    return bool(parsed.hostname and "." in parsed.hostname)

def validate_members(records: object) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError("members input must be a JSON list")
    validated: list[dict] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"member {index} must be an object")
        required = ("company", "city", "industry", "website", "description")
        if any(not isinstance(record.get(field), str) or not record[field].strip() for field in required):
            raise ValueError(f"member {index} is missing a required text field")
        validated.append({field: record[field].strip() for field in required})
    return validated

def init_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.execute("""CREATE TABLE IF NOT EXISTS report_history (
        normalized_company TEXT NOT NULL,
        reported_on TEXT NOT NULL,
        PRIMARY KEY (normalized_company, reported_on)
        )""")
    return database

def was_reported_recently(database: sqlite3.Connection, company: str, as_of: dt.date) -> bool:
    cutoff = as_of - dt.timedelta(days=90)
    row = database.execute(
        "SELECT 1 FROM report_history WHERE normalized_company = ? AND reported_on >= ? LIMIT 1",
        (normalize_company(company), cutoff.isoformat()),
    ).fetchone()
    return row is not None

def score_record(record: dict) -> tuple[int, list[str]]:
    searchable = f"{record['industry']} {record['description']}".casefold()
    score = 40
    reasons = ["eligible commercial industry"]
    for signal, phrases in BUYING_SIGNALS.items():
        if any(phrase in searchable for phrase in phrases):
            score += 12
            reasons.append(signal.replace("_", " "))
    if valid_website(record["website"]):
        score += 8
        reasons.append("valid company website")
    return min(score, 100), reasons

def build_report(members: list[dict], existing_customers: set[str], database: sqlite3.Connection, as_of: dt.date, limit: int = 20) -> dict:
    excluded = {"existing_customer": 0, "recently_reported": 0, "outside_territory": 0, "ineligible_industry": 0, "invalid_website": 0}
    leads: list[Lead] = []
    normalized_customers = {normalize_company(name) for name in existing_customers}
    for record in members:
        if record["city"] not in ALLOWED_CITIES:
            excluded["outside_territory"] += 1
            continue
        industry = record["industry"].casefold()
        if industry not in ELIGIBLE_INDUSTRIES:
            excluded["ineligible_industry"] += 1
            continue
        if not valid_website(record["website"]):
            excluded["invalid_website"] += 1
            continue
        if normalize_company(record["company"]) in normalized_customers:
            excluded["existing_customer"] += 1
            continue
        if was_reported_recently(database, record["company"], as_of):
            excluded["recently_reported"] += 1
            continue
        score, reasons = score_record(record)
        leads.append(Lead(record["company"], record["city"], record["industry"], record["website"], score, reasons))
    ranked = sorted(leads, key=lambda lead: (-lead.score, lead.company.casefold()))[:limit]
    with database:
        database.executemany("INSERT OR IGNORE INTO report_history VALUES (?, ?)", [(normalize_company(lead.company), as_of.isoformat()) for lead in ranked])
    return {
        "schema_version": 1,
        "generated_on": as_of.isoformat(),
        "territory": sorted(ALLOWED_CITIES),
        "lead_count": len(ranked),
        "leads": [asdict(lead) for lead in ranked],
        "exclusions": excluded,
        "safety": {"companies_contacted": False, "forms_submitted": False},
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reviewable fictional prospect report.")
    parser.add_argument("--members", type=Path, required=True)
    parser.add_argument("--customers", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    members = validate_members(load_json(args.members))
    customers_raw = load_json(args.customers)
    if not isinstance(customers_raw, list) or not all(isinstance(item, str) for item in customers_raw):
        raise ValueError("customers input must be a JSON list of company names")
    database = init_database(args.database)
    try:
        report = build_report(members, set(customers_raw), database, args.as_of)
    finally:
        database.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
