"""Loader for the vendored emission-factor corpus at ``data/emission-factors.json``.

The corpus is owned by the companion project carbon-lens and vendored here rather
than depended on, because this project has to run inside a GitHub Action with no
install step. Keeping a byte-identical copy is the point: the two projects used to
publish different numbers for the same physical quantity while both citing IPCC
AR5, which is exactly what a shared, versioned corpus prevents.

Refresh it with:

    curl -sSfo data/emission-factors.json \\
      https://raw.githubusercontent.com/peterklingelhofer/carbon-lens/main/data/emission-factors.json

Loading is strict. A factor must either resolve its citekey against
``docs/CITATIONS.csl.json`` or declare itself an assumption (no citekey, an
``assumption`` string, evidence tier E). Anything else raises at import, so a
factor can never reach a reported number with no stated basis at all.

Stdlib only, and no syntax newer than Python 3.9.
"""

import json
import os
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_PATH = os.path.join(ROOT, "data", "emission-factors.json")
CITATIONS_PATH = os.path.join(ROOT, "docs", "CITATIONS.csl.json")

# Corpus key -> the name this project's providers already use. Only the names
# differ; the values must not.
LOCAL_NAMES = {"natural_gas": "gas"}

VALID_TIERS = {"A", "B", "C", "D", "E"}


class FactorCorpusError(RuntimeError):
    """The corpus is missing, malformed, or has a factor with no stated basis."""


def _citekeys() -> set:
    """Every citekey defined in the CSL-JSON corpus."""
    try:
        with open(CITATIONS_PATH) as fh:
            entries = json.load(fh)
    except OSError as exc:
        raise FactorCorpusError(f"citation corpus not found at {CITATIONS_PATH}") from exc
    except ValueError as exc:
        raise FactorCorpusError(f"citation corpus is not valid JSON: {exc}") from exc
    return {entry["id"] for entry in entries if "id" in entry}


def _check(record: dict, citekeys: set) -> None:
    """Enforce the corpus contract on one record."""
    key = record.get("key")
    if not key:
        raise FactorCorpusError(f"factor record has no 'key': {record!r}")

    tier = record.get("evidence_tier")
    if tier not in VALID_TIERS:
        raise FactorCorpusError(f"{key}: evidence_tier {tier!r} is not A-E")

    citation = record.get("citation")
    if citation is None:
        # No source. Only allowed as an explicitly declared tier-E assumption, so
        # an unsourced number can never pass silently as a cited one.
        if not record.get("assumption"):
            raise FactorCorpusError(
                f"{key} has no citation and no 'assumption' explaining why. Every "
                "factor must state its basis; write the assumption down instead."
            )
        if tier != "E":
            raise FactorCorpusError(f"{key} is an assumption but claims evidence tier {tier!r}")
    elif citation not in citekeys:
        raise FactorCorpusError(
            f"{key} cites {citation!r}, which is not a citekey in CITATIONS.csl.json"
        )

    if record.get("storage"):
        if record.get("value") is not None:
            raise FactorCorpusError(f"{key} is storage and must have a null value")
    elif not isinstance(record.get("value"), (int, float)):
        raise FactorCorpusError(f"{key} has a non-numeric value {record.get('value')!r}")


def load() -> dict:
    """Parse and validate the corpus. Raises on any contract breach."""
    try:
        with open(CORPUS_PATH) as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise FactorCorpusError(f"emission-factor corpus not found at {CORPUS_PATH}") from exc
    except ValueError as exc:
        raise FactorCorpusError(f"emission-factor corpus is not valid JSON: {exc}") from exc

    citekeys = _citekeys()
    records = {}
    for record in doc.get("factors", []):
        _check(record, citekeys)
        if record["key"] in records:
            raise FactorCorpusError(f"duplicate factor key {record['key']!r}")
        records[record["key"]] = record

    if "other" not in records:
        raise FactorCorpusError("corpus must define an 'other' fallback factor")
    return {"meta": doc, "records": records}


_CORPUS = load()
_RECORDS = _CORPUS["records"]

CORPUS_VERSION = _CORPUS["meta"].get("corpus_version", "unknown")

# Generation factors under this project's local names. Storage keys are absent:
# no factor applies to them, they are excluded from the mix instead.
FUEL_FACTORS = {}
for _key, _record in _RECORDS.items():
    if _record.get("storage") or _record.get("value") is None:
        continue
    FUEL_FACTORS[LOCAL_NAMES.get(_key, _key)] = _record["value"]

# Keys whose value is an assumption rather than a citation, so a caller can flag
# a reported number that leans on one.
ASSUMED_FUELS = frozenset(
    LOCAL_NAMES.get(k, k) for k, r in _RECORDS.items() if r.get("citation") is None
)


def citation_for(fuel: str) -> Optional[str]:
    """Citekey backing a fuel's factor, or None when the factor is an assumption."""
    for key, record in _RECORDS.items():
        if LOCAL_NAMES.get(key, key) == fuel:
            return record.get("citation")
    return None
