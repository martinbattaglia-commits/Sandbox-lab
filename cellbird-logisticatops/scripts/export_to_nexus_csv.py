#!/usr/bin/env python3
"""Export the Marketing and high-priority CRM lanes from Sellbird 0.4.

Run with the project venv Python. The database is opened read-only. Company
fit and service line use the Logística TOPS qualification rules.
High-priority companies stay in the CRM lane without a verified contact.
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

from lead_qualification import ANMAT, DISCARDED, is_decision_maker, qualified_companies


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = (
    "empresa", "sitio_web", "linea_servicio", "fit_score", "senales_detectadas",
    "motivo_seleccion", "contacto_nombre", "contacto_cargo", "email",
    "email_estado", "origen_dato", "fecha_extraccion",
)


MARKETING_COLUMNS = (
    "empresa", "dominio", "linea_negocio", "fit_score", "contacto_nombre",
    "cargo", "email", "senales_detectadas", "origen_fuente",
)
CRM_COLUMNS = (
    *MARKETING_COLUMNS, "email_estado", "borrador_sugerido", "motivo_seleccion",
    "senal_activa", "decisor_identificado", "listo_para_propuesta", "precio_desde_usd_m2",
)
BUYING_SIGNALS = {"HIRING_ROLE", "FUNDING_ROUND", "RUNNING_GOOGLE_ADS", "RUNNING_META_ADS"}


def contact_email(contact):
    email = (contact.get("email") or "").strip()
    status = (contact.get("email_status") or "").strip().lower()
    if email and not status:
        status = "verified" if contact.get("email_verified") else "guess"
    return (email if status != "invalid" else ""), status


def contact_name(contact):
    return " ".join(value for key in ("first_name", "last_name")
                    if (value := (contact.get(key) or "").strip()))


def data_source(company, contact):
    values = (company["source"], company["source_url"], contact.get("source"), contact.get("source_url"))
    return " | ".join(dict.fromkeys(value for value in values if value))


def pending_drafts(db):
    # In 0.4, prospects is the people table. Both joins are optional so an
    # absent person or draft cannot remove a company from consideration.
    rows = db.execute(
        """
        SELECT p.id AS prospect_id, draft.subject, draft.body
        FROM companies AS c
        LEFT JOIN prospects AS p ON p.company_id = c.id
        LEFT JOIN outbox AS draft ON draft.id = (
            SELECT o.id FROM outbox AS o
            WHERE o.prospect_id = p.id AND o.kind = 'sequence' AND o.step = 1
              AND o.status = 'pending_review' AND TRIM(COALESCE(o.body, '')) != ''
              AND LOWER(TRIM(o.to_email)) = LOWER(TRIM(p.email))
            ORDER BY o.updated_at DESC, o.created_at DESC, o.id DESC
            LIMIT 1
        )
        WHERE draft.id IS NOT NULL
        """
    )
    return {row["prospect_id"]: "\n".join(
        value for value in (row["subject"], row["body"]) if value
    ) for row in rows}


def suggested_draft(segment):
    if segment == ANMAT:
        return ("espacio para regulados\nOfrecemos en Barracas almacenamiento para dispositivos médicos "
                "e insumos farmacéuticos con ambiente controlado de 20 a 26 °C, desde USD 45/m², sujeto "
                "a revisar los requisitos de su producto. ¿Le serviría evaluar el espacio que necesita?")
    return ("capacidad de depósito\nOfrecemos en Barracas almacenamiento de cargas generales desde "
            "USD 15/m², según el volumen y la operación. También podemos revisar la recepción de "
            "contenedores y la preparación de pedidos. ¿Le serviría evaluar el espacio que necesita?")


def export_lanes(db, config):
    drafts = pending_drafts(db)
    marketing, crm = [], []
    for company, contacts, signals, rating in qualified_companies(db, config):
        if rating.segment == DISCARDED or rating.score < 60:
            continue
        base = {
            "empresa": company["name"] or company["domain"],
            "dominio": company["domain"] or "",
            "linea_negocio": rating.segment,
            "fit_score": rating.score,
            "senales_detectadas": detected_signals(company, signals),
        }

        def row_for(contact):
            email, _ = contact_email(contact)
            return {
                **base, "contacto_nombre": contact_name(contact),
                "cargo": contact.get("title") or "", "email": email,
                "origen_fuente": data_source(company, contact),
            }

        available = [contact for contact in contacts if contact["status"] != "lost"]
        if rating.score < 80:
            marketing.extend(row_for(contact) for contact in available
                             if (contact["email"] or "").strip()
                             and (contact["email_status"] or "").strip().lower() == "verified")
            continue

        contact = max(available, key=lambda person: (
            is_decision_maker(person), contact_email(person)[1] == "verified",
            bool(contact_email(person)[0]),
        ), default={})
        decision_maker = bool(contact and is_decision_maker(contact))
        active_signal = any(signal["signal_code"] in BUYING_SIGNALS for signal in signals)
        crm.append({
            **row_for(contact),
            "email_estado": contact_email(contact)[1],
            "borrador_sugerido": drafts.get(contact.get("id")) or suggested_draft(rating.segment),
            "motivo_seleccion": " | ".join(rating.reasons),
            "senal_activa": "si" if active_signal else "no",
            "decisor_identificado": "si" if decision_maker else "no",
            "listo_para_propuesta": "si" if decision_maker and active_signal else "no",
            "precio_desde_usd_m2": 45 if rating.segment == ANMAT else 15,
        })
    return marketing, crm


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
            email, status = contact_email(contact)
            yield {
                **base,
                "contacto_nombre": contact_name(contact),
                "contacto_cargo": contact.get("title") or "",
                "email": email,
                "email_estado": status,
                "origen_dato": data_source(company, contact),
            }


def spreadsheet_cell(value):
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


def write_csv(output, rows, columns):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
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
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--out-dir", type=Path, default=ROOT / "out")
    outputs.add_argument("--out", type=Path, help="Exportación general anterior en un solo CSV")
    args = parser.parse_args()
    database = args.db.resolve()
    destinations = [args.out] if args.out else [
        args.out_dir / "nexus_marketing_import.csv",
        args.out_dir / "nexus_crm_high_priority.csv",
    ]
    if database in {path.resolve() for path in destinations}:
        parser.error("La ruta del CSV debe ser distinta a la de la base SQLite.")
    try:
        config = load_config(str(ROOT / "cellbird.local.yaml"))
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            if args.out:
                extracted = datetime.now(timezone.utc).isoformat(timespec="seconds")
                datasets = [(COLUMNS, list(export_rows(db, config, extracted)))]
            else:
                marketing, crm = export_lanes(db, config)
                datasets = [(MARKETING_COLUMNS, marketing), (CRM_COLUMNS, crm)]
        for destination, (columns, rows) in zip(destinations, datasets, strict=True):
            count = write_csv(destination, rows, columns)
            print(f"Filas exportadas: {count} -> {destination}")
    except (ConfigError, OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"No se pudo exportar: {error}\n")


if __name__ == "__main__":
    main()
