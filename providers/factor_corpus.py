"""Loader for the vendored emission-factor corpus at ``data/emission-factors.json``.

The corpus is owned by the companion project carbon-lens and vendored here rather
than depended on, because this project has to run inside a GitHub Action with no
install step. A byte-identical copy matters because the two projects used to
publish different numbers for the same physical quantity while both citing IPCC
AR5. A shared, versioned corpus prevents that mismatch.

Refresh it with:

    curl -sSfo data/emission-factors.json \\
      https://raw.githubusercontent.com/peterklingelhofer/carbon-lens/main/data/emission-factors.json

Loading is strict. A factor must either resolve its citekey against the
``CITATION_IDS`` generated from ``docs/CITATIONS.csl.json`` or declare itself an
assumption (no citekey, an ``assumption`` string, evidence tier E). Anything else
raises at import, so a factor can never reach a reported number with no stated
basis at all. The generated ids are used because they ship in the wheel and the
Docker image, where ``docs/`` does not.
"""

import json
import os

from citations_generated import CITATION_IDS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_PATH = os.path.join(ROOT, "data", "emission-factors.json")

# Corpus key -> the name this project's providers already use. Only the names
# differ between the two projects, never the values
LOCAL_NAMES = {"natural_gas": "gas"}

VALID_TIERS = {"A", "B", "C", "D", "E"}


class FactorCorpusError(RuntimeError):
    """The corpus is missing, malformed, or has a factor with no stated basis."""


def _check(record: dict) -> None:
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
        # an unsourced number can never pass silently as a cited one
        if not record.get("assumption"):
            raise FactorCorpusError(
                f"{key} has no citation and no 'assumption' explaining why. Every "
                "factor must state its basis, so write the assumption down."
            )
        if tier != "E":
            raise FactorCorpusError(f"{key} is an assumption but claims evidence tier {tier!r}")
    elif citation not in CITATION_IDS:
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

    records = {}
    for record in doc.get("factors", []):
        _check(record)
        if record["key"] in records:
            raise FactorCorpusError(f"duplicate factor key {record['key']!r}")
        records[record["key"]] = record

    if "other" not in records:
        raise FactorCorpusError("corpus must define an 'other' fallback factor")
    return {"meta": doc, "records": records}


_CORPUS = load()
_RECORDS = _CORPUS["records"]

CORPUS_VERSION = _CORPUS["meta"].get("corpus_version", "unknown")

# Generation factors under this project's local names. Storage keys are absent
# because storage is excluded from the mix and carries no factor
FUEL_FACTORS = {}
for _key, _record in _RECORDS.items():
    if _record.get("storage") or _record.get("value") is None:
        continue
    FUEL_FACTORS[LOCAL_NAMES.get(_key, _key)] = _record["value"]
