#!/usr/bin/env python3
"""Run just Sellbird's Writer with the TOPS qualification applied first."""

import argparse
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
# Sellbird resolves prompt, skill and config paths on import.
os.environ["SELLBIRD_HOME"] = str(ROOT)

from sellbird.agents.writer import Writer
from sellbird.brain import Brain
from sellbird.config import ConfigError, load_config
from sellbird.state import StateManager, WRITABLE_EMAIL_STATUSES

from lead_qualification import DISCARDED, qualified_companies


class QualifiedWriterState(StateManager):
    def __init__(self, db_path, eligible):
        super().__init__(str(db_path))
        self.eligible = eligible

    async def get_prospects_by_status(self, status):
        prospects = await super().get_prospects_by_status(status)
        if status != "new":
            return prospects
        qualified = []
        for prospect in prospects:
            rating = self.eligible.get(prospect.id)
            if rating is None:
                continue
            note = f"Línea aprobada para este borrador: {rating.segment}."
            qualified.append(prospect.model_copy(update={
                "score": rating.score,
                "industry": rating.segment,
                "personalization_notes": f"{note} {prospect.personalization_notes}".strip(),
            }))
        return qualified


async def draft(database, config, eligible):
    state = QualifiedWriterState(database, eligible)
    if not await state.get_setting("sending_paused"):
        raise ValueError("Primero activá la pausa local con sellbird sending pause.")
    brain = Brain(state, config.brain)
    problem = await brain.preflight()
    if problem:
        raise ValueError(problem)
    await Writer(brain, state, config).run()


def get_eligible(database, config, threshold):
    eligible = {}
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        for _, contacts, _, rating in qualified_companies(db, config):
            if rating.segment == DISCARDED or rating.score < threshold:
                continue
            for contact in contacts:
                if (contact["status"] == "new" and contact["email"]
                        and contact["email_status"] in WRITABLE_EMAIL_STATUSES):
                    eligible[contact["id"]] = rating
    return eligible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Cuenta elegibles sin ejecutar el Writer")
    args = parser.parse_args()
    database = ROOT / "data/sellbird.db"
    try:
        config = load_config(str(ROOT / "cellbird.local.yaml"))
        email = config.channels.email
        if email.enabled or not email.require_approval or email.max_daily_sends != 0:
            raise ValueError("El Writer de prueba requiere correo deshabilitado, cupo cero y aprobación manual.")
        threshold = max(60, email.min_write_score)
        eligible = get_eligible(database, config, threshold)
        print(f"Contactos elegibles para el Writer con umbral {threshold}: {len(eligible)}", flush=True)
        if eligible and not args.dry_run:
            asyncio.run(draft(database, config, eligible))
    except (ConfigError, ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"No se pudo ejecutar el Writer: {error}\n")


if __name__ == "__main__":
    main()
