#!/usr/bin/env python3
"""Export Nexus CRM CSV from the local Sellbird 0.4 SQLite schema.

Run with the project venv Python. The database is opened read-only. Company
fit and service line use the Logística TOPS qualification rules.
Companies stay in the file even when all of their contacts are suppressed.
"""

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile

from sellbird.config import ConfigError, load_config

from lead_qualification import qualified_companies


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = (
    "empresa", "sitio_web", "linea_servicio", "fit_score", "senales_detectadas",
    "motivo_seleccion", "contacto_nombre", "contacto_cargo", "email",
    "email_estado", "origen_dato", "fecha_extraccion",
)


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
    for company, contacts, signals, rating in qualified_companies(db, config):
        domain = company["domain"] or ""
        company_source = company["source"] or ""
        selection = [f"Fuente: {company_source}"] if company_source else []
        selection.extend(rating.reasons)
        base = {
            "empresa": company["name"] or domain,
            "sitio_web": company["website"] or (f"https://{domain}" if domain else ""),
            "linea_servicio": rating.segment,
            "fit_score": rating.score,
            "senales_detectadas": detected_signals(company, signals),
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
