#!/usr/bin/env python3
"""Export Nexus CRM CSV from the local Sellbird 0.4 SQLite schema.

Run with the project venv Python. The database is opened read-only. Company
fit comes from Sellbird, and the service line is inferred from business text.
Companies stay in the file even when all of their contacts are suppressed.
"""

import argparse
from collections import defaultdict
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata

from sellbird.config import ConfigError, load_config
from sellbird.fit import company_fit
from sellbird.gate import do_not_contact_match
from sellbird.state import StateManager


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = (
    "empresa", "sitio_web", "linea_servicio", "fit_score", "senales_detectadas",
    "motivo_seleccion", "contacto_nombre", "contacto_cargo", "email",
    "email_estado", "origen_dato", "fecha_extraccion",
)
REGULATED = re.compile(
    r"\b(?:anmat|farmac\w*|medicament\w*|insumos? medic\w*|"
    r"productos? medic\w*|dispositivos? medic\w*)\b"
)
GENERAL = re.compile(
    r"\b(?:distribu\w*|importa\w*|fabrica\w*|industria\w*|"
    r"mayorista\w*|repuestos?|cargas? generales)\b"
)


def service_line(company):
    text = " ".join(company[key] or "" for key in ("name", "industry", "description"))
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    if REGULATED.search(normalized):
        return "Productos Regulados"
    if GENERAL.search(normalized):
        return "Cargas Generales"
    return "Por clasificar"


def rows_by_company(db, sql):
    grouped = defaultdict(list)
    for row in db.execute(sql):
        grouped[row["company_id"]].append(dict(row))
    return grouped


def detected_signals(company, current):
    signals = []
    for row in current:
        code = row["signal_code"]
        if row["value_text"]:
            signals.append(f"{code}: {row['value_text']}")
        elif row["value_num"] not in (None, 1):
            signals.append(f"{code}: {row['value_num']:g}")
        else:
            signals.append(code)
    try:
        legacy = json.loads(company["signals_json"] or "[]")
    except json.JSONDecodeError:
        legacy = []
    if isinstance(legacy, list):
        for item in legacy:
            if isinstance(item, dict) and item.get("type"):
                signals.append(f"{item['type']}: {item['detail']}" if item.get("detail")
                               else item["type"])
    return " | ".join(sorted(set(signals)))


def export_rows(db, config, extracted):
    companies = db.execute("SELECT * FROM companies ORDER BY name COLLATE NOCASE, id")
    prospects = rows_by_company(
        db, "SELECT * FROM prospects ORDER BY score DESC, created_at, id"
    )
    fit_evidence = rows_by_company(
        db, "SELECT * FROM observations WHERE signal_code IN "
        "('DECISION_MAKER_TITLE', 'CONTACT_FORM_URL', 'HIRING_ROLE')"
    )
    # Reuse Sellbird's cohort rules for current positive observations.
    signals = rows_by_company(db, StateManager._CURRENT_CTE + "SELECT * FROM positive")
    dnc = list(config.compliance.do_not_contact)
    suppressed = [row["value"] for row in db.execute(
        "SELECT value FROM suppressions WHERE kind IN ('domain', 'address')"
    )]
    exclusions = dnc + suppressed

    for record in companies:
        company = dict(record)
        company_id = company["id"]
        domain = company["domain"] or ""
        candidates = prospects[company_id]
        contacts = [
            contact for contact in candidates
            if contact["status"] != "opted_out"
            and not do_not_contact_match(contact["email"] or "", exclusions)
        ]
        if domain and do_not_contact_match(f"x@{domain}", exclusions):
            contacts = []
        score, reasons = company_fit(company, contacts, fit_evidence[company_id], config, exclusions)
        company_source = company["source"] or ""
        selection = [f"Fuente: {company_source}"] if company_source else []
        selection.extend(reasons)
        base = {
            "empresa": company["name"] or domain,
            "sitio_web": company["website"] or (f"https://{domain}" if domain else ""),
            "linea_servicio": service_line(company),
            "fit_score": score,
            "senales_detectadas": detected_signals(company, signals[company_id]),
            "motivo_seleccion": " | ".join(selection),
            "fecha_extraccion": extracted,
        }
        for contact in contacts or [{}]:
            email = (contact.get("email") or "").strip()
            status = (contact.get("email_status") or "").strip().lower()
            if email and not status:
                status = "verified" if contact["email_verified"] else "guess"
            sources = (
                company_source, company["source_url"], contact.get("source"),
                contact.get("source_url"),
            )
            yield {
                **base,
                "contacto_nombre": " ".join(
                    name for key in ("first_name", "last_name")
                    if (name := (contact.get(key) or "").strip())
                ),
                "contacto_cargo": contact.get("title") or "",
                "email": email if status != "invalid" else "",
                "email_estado": status,
                "origen_dato": " | ".join(dict.fromkeys(source for source in sources if source)),
            }


def spreadsheet_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def write_csv(output, rows):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            count = 0
            for row in rows:
                writer.writerow({key: spreadsheet_cell(value) for key, value in row.items()})
                count += 1
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/sellbird.db")
    parser.add_argument("--out", type=Path, default=ROOT / "out/leads_nexus_import.csv")
    args = parser.parse_args()
    database = args.db.resolve()
    if args.out.resolve() == database:
        parser.error("La ruta del CSV debe ser distinta a la de la base SQLite.")
    try:
        config = load_config(str(ROOT / "cellbird.local.yaml"))
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            extracted = datetime.now(timezone.utc).isoformat(timespec="seconds")
            count = write_csv(args.out, export_rows(db, config, extracted))
    except (ConfigError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"No se pudo exportar: {error}\n")
    print(f"Filas exportadas: {count} -> {args.out}")


if __name__ == "__main__":
    main()
