"""Deterministic TOPS qualification shared by the CRM export and draft runner."""

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

import yaml

from sellbird.fit import current_roles
from sellbird.gate import do_not_contact_match
from sellbird.state import StateManager


ANMAT = "ANMAT"
GENERAL = "CARGAS_GENERALES"
DISCARDED = "DESCARTADO"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "cellbird.local.yaml"
LOGISTICS_SERVICE = (
    r"operador(?:es)? logistic[oa]s?|[34]pl|fulfil{1,2}ment|cross[ -]?docking|"
    r"(?:almacenamiento|almacenaje|deposito) para terceros|depositos? fiscal(?:es)?|"
    r"transportes? (?:de )?cargas?|fletes?|correos? privados?|"
    r"paqueteria|ultima milla|freight forward(?:ing|ers?)"
)
SERVICE_PROVIDER = (
    r"somos|especializad[oa]s? en|nos dedicamos a|ofrecemos|brindamos|prestamos|"
    r"proveemos|empresa de|servicios? (?:de |incluyen )|negocio principal (?:es|incluye)|"
    r"oferta (?:principal )?(?:es|incluye)"
)
OWN_MERCHANDISE = (
    r"(?:nuestr[oa]s?|sus?) (?:propia[s]? )?(?:mercaderia|productos?|pedidos?|ventas?)|"
    r"mercaderia propia|productos? propios?|(?:en|con) (?:cada|tus|sus) compras?|"
    r"entrega de (?:la |su )?compra|contratamos|tercerizamos|trabajamos con|usamos|"
    r"(?:via|a traves de|mediante) (?:un |una )?"
    r"(?:operador|correo|transportista|[34]pl|fulfil{1,2}ment)"
)
EXTERNAL_CUSTOMERS = r"para terceros|para empresas|para tiendas|para comercios|para tu negocio|para e[- ]?commerce"
# Strip wrappers used in business listings, then match the whole brand.
BRAND_PREFIX = r"^(?:(?:grupo|transportes?|correo|operador logistico) )+"
BRAND_SUFFIX = r"(?: (?:s a u|s r l|s a|sau|srl|sa|argentina|group|grupo))+$"

# Agustín Magaldi 1765 and Pedro de Luján 3159, respectively.
SITES = ((-34.655221, -58.392065), (-34.656943, -58.3911209))
COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$")
REGULATED = (
    r"anmat|farmac\w*|medicament\w*|laboratorios? (?:de )?(?:"
    r"analisis clinicos|especialidades medicinales)|productos? regulados?|"
    r"(?:dispositivos?|productos?|insumos?|equipos?) medic\w*|insumos? hospitalari\w*"
)
GENERAL_SECTOR = (
    r"distribu\w*|importa\w*|fabrica\w*|industria\w*|mayorista\w*|"
    r"repuestos?|cargas? generales|mercaderia|inventario|pallets?|contenedores?"
)
VOLUME = (
    r"importa\w*|contenedores?|desconsolida\w*|alto volumen|"
    r"gran volumen|volumen (?:alto|recurrente)|\d+\s+(?:pallets?|contenedores?)"
)
PHYSICAL = r"mecalux|racks? (?:selectivos?|drive-in)|paletizad\w*|pallets?|estibas?"
INCOMPATIBLE = r"granel|mercaderia sobredimensionada|cadena de frio|carga peligrosa"
TEMPERATURE = r"20\s*(?:°?\s*c\s*)?(?:a|y|[-–—])\s*26\s*°?\s*c\b"
NO_LICENSED_SITE = (
    r"(?:sin|no (?:cuenta|contamos|cuentan) con) "
    r"(?:un )?(?:deposito|almacen) (?:propio habilitado|habilitado propio|habilitado)"
)
HAS_LICENSED_SITE = (
    r"(?<!no )(?:cuenta|cuentan|contamos) con (?:un )?(?:deposito|almacen) "
    r"(?:propio habilitado|habilitado propio)"
)
AUTHORITY = (
    r"duen[oa]|soci[oa]|president\w*|ceo|coo|fundador\w*|"
    r"(?:gerent\w*|gerencia|jef\w*|director\w*|responsable|head) "
    r"(?:de |de la |del |of )?(?:supply chain|logistica|operaciones|"
    r"comercial|compras|calidad|regulacion|asuntos regulatorios|comex|comercio exterior|tecnic\w*)"
)


def normalize(value):
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def contains(pattern, text):
    return re.search(rf"\b(?:{pattern})\b", text) is not None


def brand_key(name):
    words = " ".join(re.findall(r"[a-z0-9]+", normalize(name)))
    words = re.sub(BRAND_PREFIX, "", words)
    words = re.sub(BRAND_SUFFIX, "", words)
    return words.replace(" ", "")


def load_brand_keys():
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        brands = yaml.safe_load(stream)["qualification"]["excluded_brands"]
    if not isinstance(brands, list) or not brands or any(
        not isinstance(brand, str) or not brand_key(brand) for brand in brands
    ):
        raise ValueError("qualification.excluded_brands debe ser una lista de marcas.")
    return {brand_key(brand) for brand in brands}


def offers_logistics(company):
    category = normalize(company.get("industry")).strip()
    # Keep clauses separate so a vendor mention elsewhere does not turn the
    # company's merchandise shipping into a third-party service offering.
    description = normalize(company.get("description"))
    contrary_evidence = False
    for clause in re.split(r"[.!?;\n]+", description):
        own = contains(OWN_MERCHANDISE, clause)
        contrary_evidence |= own
        if not contains(LOGISTICS_SERVICE, clause):
            continue
        if contains(r"no (?:ofrecemos|prestamos|brindamos|somos|realizamos)", clause):
            contrary_evidence = True
            continue
        external = contains(EXTERNAL_CUSTOMERS, clause)
        service_label = re.fullmatch(
            rf"\s*(?:{LOGISTICS_SERVICE})(?: (?:integral(?:es)?|nacional(?:es)?|internacional(?:es)?))*\s*",
            clause,
        )
        if not own and (external or contains(SERVICE_PROVIDER, clause) or service_label):
            return True
    return not contrary_evidence and re.fullmatch(LOGISTICS_SERVICE, category) is not None


def company_exclusion(company, exclusions, brands):
    domain = company["domain"] or ""
    website = company["website"] or ""
    try:
        host = urlsplit(website if "://" in website else f"//{website}").hostname
    except ValueError:
        host = None
    for address in (domain, host):
        if address and do_not_contact_match(f"x@{address}", exclusions):
            return "Empresa excluida por supresión de dominio"
    name = company["name"] or ""
    identity = name.split("(", 1)[0]
    if brand_key(name) in brands or brand_key(identity) in brands:
        return "Empresa excluida por marca competidora"
    if offers_logistics(company):
        return "Ofrece servicios logísticos a terceros"
    return ""


def proximity(location):
    coords = COORDINATES.fullmatch(location or "")
    if coords:
        lat, lon = map(float, coords.groups())
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False
        for site_lat, site_lon in SITES:
            h = (math.sin(math.radians(lat - site_lat) / 2) ** 2
                 + math.cos(math.radians(lat)) * math.cos(math.radians(site_lat))
                 * math.sin(math.radians(lon - site_lon) / 2) ** 2)
            if 2 * 6371.0088 * math.asin(math.sqrt(min(1, h))) <= 60:
                return True
        return False
    return contains(r"caba|capital federal|ciudad autonoma de buenos aires", normalize(location))


@dataclass(frozen=True)
class Qualification:
    segment: str
    score: int
    reasons: tuple[str, ...]


def qualify(company, contacts, signals, observations, exclusion=None):
    if exclusion is None and offers_logistics(company):
        exclusion = "Ofrece servicios logísticos a terceros"
    if exclusion:
        return Qualification(DISCARDED, 0, (exclusion,))

    sector = normalize(" ".join(company[key] or "" for key in ("name", "industry")))
    # A discovery category can be the search phrase itself. Use it for routing,
    # and require company description for operational claims.
    description = normalize(" ".join(company[key] or "" for key in ("description", "notes")))
    business = f"{sector} {description}"
    regulated = contains(REGULATED, business) or contains(r"laboratorio\w*", sector)
    if not regulated and not contains(GENERAL_SECTOR, business):
        return Qualification(DISCARDED, 0, ("Sin evidencia de encaje con las dos líneas",))

    near = proximity(company["location"])
    roles = " | ".join(normalize(row["value_text"]) for row in current_roles(observations))
    codes = {signal["signal_code"] for signal in signals}
    decision_maker = any(
        (contact["first_name"] or "").strip()
        and (contact["last_name"] or "").strip()
        and contact["status"] != "lost"
        and contains(AUTHORITY, normalize(contact["title"]))
        for contact in contacts
    )
    if regulated:
        segment = ANMAT
        checks = (
            (True, 25, "Rubro sanitario o regulado"),
            (contains(TEMPERATURE, description), 10, "Operación ambiente de 20 a 26 °C"),
            (near, 20, "Ubicación dentro de 60 km"),
            (contains(r"importa\w*|distribu\w*", description)
             and contains(NO_LICENSED_SITE, description)
             and not contains(HAS_LICENSED_SITE, description),
             20, "Importación o distribución sin depósito habilitado propio"),
            (contains(r"calidad|regulacion|regulatori\w*|comex|comercio exterior", roles),
             15, "Vacante en calidad, regulación o comex"),
            (decision_maker, 10, "Decisor identificado"),
        )
    else:
        segment = GENERAL
        checks = (
            (contains(VOLUME, description), 30, "Volumen, importación o contenedores"),
            (near, 25, "Ubicación dentro de 60 km"),
            (bool(codes & {"FUNDING_ROUND", "RUNNING_GOOGLE_ADS", "RUNNING_META_ADS"})
             or contains(r"expansion|crecimiento|ampliacion de (?:planta|deposito)", description)
             or contains(r"logistica|supply chain|deposito|almacen|warehouse", roles),
             25, "Crecimiento, vacante logística o pauta"),
            (contains(PHYSICAL, description) and not contains(INCOMPATIBLE, description),
             10, "Carga paletizada o apta para racks Mecalux"),
            (decision_maker, 10, "Decisor identificado"),
        )
    reasons = tuple(f"{label} (+{points})" for earned, points, label in checks if earned)
    return Qualification(segment, sum(points for earned, points, _ in checks if earned),
                         reasons or ("Sin evidencia puntuable",))


def rows_by_company(db, sql):
    grouped = defaultdict(list)
    for row in db.execute(sql):
        grouped[row["company_id"]].append(dict(row))
    return grouped


def qualified_companies(db, config):
    prospects = rows_by_company(db, "SELECT * FROM prospects ORDER BY score DESC, created_at, id")
    observations = rows_by_company(db, "SELECT * FROM observations WHERE signal_code = 'HIRING_ROLE'")
    signals = rows_by_company(db, StateManager._CURRENT_CTE + "SELECT * FROM positive")
    brands = load_brand_keys()
    exclusions = list(config.compliance.do_not_contact)
    exclusions.extend(row["value"] for row in db.execute(
        "SELECT value FROM suppressions WHERE kind IN ('domain', 'address')"
    ))

    for record in db.execute("SELECT * FROM companies ORDER BY name COLLATE NOCASE, id"):
        company = dict(record)
        company_id = company["id"]
        exclusion = company_exclusion(company, exclusions, brands)
        contacts = [
            contact for contact in prospects[company_id]
            if not exclusion and contact["status"] != "opted_out"
            and not do_not_contact_match(contact["email"] or "", exclusions)
        ]
        active_signals = signals[company_id]
        rating = qualify(company, contacts, active_signals, observations[company_id], exclusion)
        yield company, contacts, active_signals, rating
