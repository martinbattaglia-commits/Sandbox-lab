#!/usr/bin/env python3
"""Importar empresas semilla a Sellbird 0.4.0 sin duplicar dominios."""

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from sellbird.config import ConfigError, load_config

from lead_qualification import ANMAT, GENERAL, company_exclusion, load_brand_keys


ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_domain(value):
    value = (value or "").strip()
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
        if parsed.scheme not in ("", "http", "https") or parsed.username is not None:
            return ""
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").removeprefix("www.")
    except (UnicodeError, ValueError):
        return ""
    return host if HOSTNAME.fullmatch(host) else ""


def read_csv(path, required):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not required.intersection(reader.fieldnames or []):
            raise ValueError(f"{path}: falta una columna entre {', '.join(sorted(required))}.")
        return list(reader)


def import_companies(db, rows, exclusions, brands, dry_run):
    required = {"id", "name", "domain", "website", "industry", "location", "source", "created_at", "updated_at"}
    columns = {column[1] for column in db.execute("PRAGMA table_info(companies)")}
    if missing := required - columns:
        raise ValueError(f"Esquema companies incompatible: faltan {', '.join(sorted(missing))}.")
    domains = {
        domain
        for record in db.execute("SELECT domain, website FROM companies")
        for value in record
        if (domain := normalize_domain(value))
    }
    stats = dict(leidas=0, sin_dominio=0, excluidas_clientes_o_3pl=0,
                 duplicadas_en_sellbird=0, insertadas=0)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    for line, row in enumerate(rows, start=2):
        stats["leidas"] += 1
        domain = normalize_domain(row.get("website"))
        if not domain:
            stats["sin_dominio"] += 1
            continue
        name = (row.get("company_name") or "").strip() or domain
        industry = (row.get("industry") or "").strip()
        company = dict(domain=domain, website=f"https://{domain}", name=name, industry=industry)
        if company_exclusion(company, exclusions, brands):
            stats["excluidas_clientes_o_3pl"] += 1
            continue
        if domain in domains:
            stats["duplicadas_en_sellbird"] += 1
            continue

        segment = (row.get("segment_hint") or "").strip().upper() or GENERAL
        if segment not in (ANMAT, GENERAL):
            raise ValueError(f"Fila {line}: segment_hint debe ser ANMAT o CARGAS_GENERALES.")
        location = (row.get("city") or "").strip() or "Buenos Aires"
        source = (row.get("source_tag") or "").strip() or "manual_seed_import"
        if not dry_run:
            db.execute(
                """INSERT INTO companies
                   (id, name, domain, website, industry, location, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid4().hex[:12], name, domain, company["website"],
                 f"[{segment}] {industry}".rstrip(), location, f"seed:{source}", now, now),
            )
        domains.add(domain)
        stats["insertadas"] += 1
    if dry_run:
        stats["insertables"] = stats["insertadas"]
        stats["insertadas"] = 0
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/sellbird.db")
    parser.add_argument("--seed", type=Path, default=ROOT / "data/semillas_b2b.csv")
    parser.add_argument("--exclusions", type=Path, default=ROOT / "data/clientes_activos_exclusion.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    database = args.db.resolve()
    try:
        rows = read_csv(args.seed, {"website"})
        config = load_config(str(ROOT / "cellbird.local.yaml"))
        brands = load_brand_keys()
        exclusions = ["logisticatops.com", "verotin.com.ar", *config.compliance.do_not_contact]
        if args.exclusions.exists():
            exclusions.extend(
                domain for row in read_csv(args.exclusions, {"domain", "website"})
                if (domain := normalize_domain(row.get("domain") or row.get("website")))
            )
        elif args.exclusions != ROOT / "data/clientes_activos_exclusion.csv":
            raise FileNotFoundError(args.exclusions)
        mode = "ro" if args.dry_run else "rw"
        with closing(sqlite3.connect(f"{database.as_uri()}?mode={mode}", uri=True)) as db, db:
            db.execute("BEGIN" if args.dry_run else "BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppressions'").fetchone():
                exclusions.extend(row[0] for row in db.execute(
                    "SELECT value FROM suppressions WHERE kind = 'domain'"
                ))
            stats = import_companies(db, rows, exclusions, brands, args.dry_run)
    except (ConfigError, OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"No se pudo importar: {error}\n")
    print(f"Base SQLite: {database}")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
