"""Fill the field map's two ontology-rule columns from the pinned MetaboLights policy.

Each cell says what the policy accepts in the row's ISA column for an LC-MS study:
a fixed list of terms, or the ontologies a term may come from, whether the rule is
required, and an example. Rows whose ISA column has no term rule stay empty.
Run from the repository root after editing the CSV or the pinned bundle;
backend/tests/test_field_map.py fails until the columns match.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FIELD_MAP = ROOT / "docs/resources/metabolens-metabolights-field-map.csv"
PINNED = ROOT / "backend/app/resources/metabolights"
GENERAL = "Ontology rule (general MetaboLights studies)"
HUB = "Ontology rule (MetabolomicsHub studies)"
SECTIONS = {"i_": "investigationFileControls", "s_": "sampleFileControls", "a_": "assayFileControls",
            "m_": "assignmentFileControls"}
# The policy's study categories: MetabolomicsHub MS studies, and other MetaboLights MS studies.
HUB_CATEGORY, GENERAL_CATEGORY = "ms-mhd-enabled", "ms-mhd-legacy"


def load_policy(pinned: Path = PINNED) -> tuple[dict, str]:
    """The pinned bundle's term controls and the pinned ISA template version."""
    with tarfile.open(pinned / "bundle.tar.gz") as bundle:
        data = json.load(bundle.extractfile("/data.json"))
    template_version = json.loads((pinned / "lock.json").read_text())["template_version"]
    return data["metabolights"]["validation"]["v2"]["controls"], template_version


def accession(iri: str) -> str:
    """`MS:1000073` for OBO-style IRIs; MetaboLights' own `MTBLS_000856` stays as written."""
    tail = (iri or "").rstrip("/").rsplit("/", 1)[-1]
    match = re.fullmatch(r"([A-Z][A-Za-z]*)_(\w+)", tail)
    return f"{match[1]}:{match[2]}" if match and match[1] != "MTBLS" else tail


def label(term: dict) -> str:
    code = accession(term.get("termAccessionNumber", ""))
    return f"{term['term']} ({code})" if code else term["term"]


def applies(rule: dict, category: str, template_version: str) -> bool:
    """Current LC-MS rules for one study category; superseded date and template variants are skipped."""
    criteria = rule["selectionCriteria"]
    templates, versions = criteria.get("isaFileTemplateNameFilter"), criteria.get("templateVersionFilter")
    categories = criteria.get("studyCategoryFilter")
    return (rule.get("termEnforcementLevel") != "not-applicable"
            and (not templates or "LC-MS" in templates) and (not versions or template_version in versions)
            and not criteria.get("studyCreatedBefore") and (not categories or category in categories))


def rules_for(rules: list[dict], category: str, template_version: str) -> list[dict]:
    """A rule written for the category replaces the rules written for every category."""
    matching = [rule for rule in rules if applies(rule, category, template_version)]
    specific = [rule for rule in matching if rule["selectionCriteria"].get("studyCategoryFilter")]
    return specific or matching


def example(rule: dict, illustration: str) -> str:
    """The listed term the row's illustrative value cites or names, else the policy's first."""
    terms = rule.get("terms") or []
    if not terms:
        return ""
    cited = [term for term in terms if term.get("termAccessionNumber")
             and term["termAccessionNumber"].rsplit("/", 1)[-1] in illustration]
    # Match the value itself, not the commentary after it; on a tie, the shorter term names it more exactly.
    words = set(re.findall(r"[a-z0-9]{3,}", re.split(r"[(;]", illustration, maxsplit=1)[0].lower()))

    def score(term):
        own = re.findall(r"[a-z0-9]{3,}", term["term"].lower())
        shared = len(words & set(own))
        return shared, -len(own) if shared else 0

    return f" Example: {label(cited[0] if cited else max(terms, key=score))}."


def describe(rule: dict, illustration: str) -> str:
    level = (rule.get("termEnforcementLevel") or "").capitalize()
    kind, ontologies = rule.get("validationType"), ", ".join(rule.get("ontologies") or [])
    if kind == "selected-ontology-term":
        return f"{level}. Fixed list: {', '.join(label(term) for term in rule.get('terms') or [])}."
    if kind == "child-ontology-term":
        parents = (rule.get("allowedParentOntologyTerms") or {}).get("parents") or []
        below = " or ".join(label(parent) for parent in parents)
        return f"{level}. Any term below {below}.{example(rule, illustration)}"
    if kind == "ontology-term-in-selected-ontologies":
        return f"{level}. Any term from {ontologies}.{example(rule, illustration)}"
    return f"{level}. Any ontology term.{example(rule, illustration)}"


def ontology_rule_cells(row: dict, controls: dict, template_version: str) -> tuple[str, str]:
    """The (general, MetabolomicsHub) cells for one field-map row."""
    files, _, columns = row["ISA destination"].partition("\n")
    sections = [section for prefix, section in SECTIONS.items()
                if any(name.strip().startswith(prefix) for name in files.split(";"))]
    found = [(column, controls[section][column]) for column in (part.strip() for part in columns.split(";"))
             for section in sections if column in controls.get(section, {})]
    cells = []
    for category in (GENERAL_CATEGORY, HUB_CATEGORY):
        texts = {}  # one entry per ISA column, even when two files share its name
        for column, rules in found:
            text = " Or: ".join(describe(rule, row["Typical example (illustrative)"])
                                for rule in rules_for(rules, category, template_version))
            if text:
                texts.setdefault(column, text)
        cells.append(" ".join(f"{column}: {text}" if len(texts) > 1 else text for column, text in texts.items()))
    general, hub = cells
    return general, "Same as general MetaboLights studies." if hub and hub == general else hub


def updated(text: str, controls: dict, template_version: str) -> str:
    """The CSV text with both rule columns filled before Code evidence."""
    header, *rows = list(csv.reader(io.StringIO(text, newline="")))
    keep = [index for index, name in enumerate(header) if name not in (GENERAL, HUB)]
    names = [header[index] for index in keep]
    position = names.index("Code evidence")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(names[:position] + [GENERAL, HUB] + names[position:])
    for values in rows:
        kept = [values[index] for index in keep]
        cells = ontology_rule_cells(dict(zip(names, kept)), controls, template_version)
        writer.writerow(kept[:position] + list(cells) + kept[position:])
    return out.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report whether the CSV is current; change nothing")
    args = parser.parse_args()
    with FIELD_MAP.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    result = updated(text, *load_policy())
    if args.check:
        print("Field map ontology rules are current." if result == text else "Field map ontology rules are stale.")
        return 0 if result == text else 1
    FIELD_MAP.write_text(result, encoding="utf-8", newline="")
    print(f"Updated {FIELD_MAP.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
