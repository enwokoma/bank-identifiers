#!/usr/bin/env python3
"""Offline institution lookup. Python 3.10+, standard library only."""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATTERNS = {
    "US_ABA": ("US", r"[0-9]{9}"),
    "BIC": (None, r"[A-Z0-9]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?"),
    "GB_SORT_CODE": ("GB", r"[0-9]{6}"),
    "DE_BLZ": ("DE", r"[0-9]{8}"),
    "AU_BSB": ("AU", r"[0-9]{6}"),
    "IN_IFSC": ("IN", r"[A-Z]{4}0[A-Z0-9]{6}"),
    "CA_EFT": ("CA", r"0[0-9]{8}"),
    "NL_BANK_CODE": ("NL", r"[A-Z]{4}"),
    "FR_BANK_BRANCH": ("FR", r"[0-9]{10}"),
    "ES_BANK_BRANCH": ("ES", r"[0-9]{8}"),
    "IT_BANK_BRANCH": ("IT", r"[0-9]{10}"),
}
RULES = json.loads((ROOT / "iban_rules.json").read_text())["countries"]


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD")
    return dt.date.fromisoformat(value)


def mod97(value):
    remainder = 0
    for char in value[4:] + value[:4]:
        for digit in (str(ord(char) - 55) if char.isalpha() else char):
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def validate(scheme, value, country=None):
    """Return normalized lookup key, never assert allocation or reachability."""
    scheme = scheme.upper()
    result = {"scheme": scheme, "status": "valid_format", "checksum_valid": None}
    if not isinstance(value, str) or not value.isascii():
        return {**result, "status": "invalid_format"}
    value = value.strip().upper()
    # Only documented separators are accepted; punctuation is never stripped globally.
    if scheme in {"GB_SORT_CODE", "AU_BSB"}:
        if re.fullmatch(r"[0-9]{2}-[0-9]{2}-[0-9]{2}", value) and scheme == "GB_SORT_CODE":
            value = value.replace("-", "")
        elif re.fullmatch(r"[0-9]{3}-[0-9]{3}", value) and scheme == "AU_BSB":
            value = value.replace("-", "")
    if scheme == "IBAN":
        value = value.replace(" ", "")
        # Never echo or store the account number in normal lookup output.
        if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", value):
            return {**result, "status": "invalid_format"}
        inferred_country = value[:2]
        if country and country.upper() != inferred_country:
            return {**result, "status": "country_mismatch"}
        result["country"] = inferred_country
        rule = RULES.get(inferred_country)
        if not rule:
            return {**result, "status": "unsupported_country"}
        if len(value) != rule["length"] or not re.fullmatch(rule["bban_pattern"], value[4:]):
            return {**result, "status": "invalid_format"}
        result["checksum_valid"] = mod97(value) == 1 and 2 <= int(value[2:4]) <= 98
        if not result["checksum_valid"]:
            return {**result, "status": "invalid_checksum"}
        start, end = rule["lookup_slice"]
        result["lookup"] = {"scheme": rule["lookup_scheme"], "country": inferred_country,
                            "value": value[start:end]}
        result["bank_code"] = value[slice(*rule["bank_slice"])]
        if rule.get("branch_slice"):
            result["branch_code"] = value[slice(*rule["branch_slice"])]
        result["validation_scope"] = "national_structure_and_iban_checksum"
        return result
    if scheme not in PATTERNS:
        return {**result, "status": "unsupported_scheme"}
    fixed_country, pattern = PATTERNS[scheme]
    if not re.fullmatch(pattern, value):
        return {**result, "status": "invalid_format"}
    inferred_country = value[4:6] if scheme == "BIC" else fixed_country
    if country and country.upper() != inferred_country:
        return {**result, "status": "country_mismatch"}
    result["country"] = inferred_country
    if scheme == "US_ABA":
        prefix = int(value[:2])
        if prefix not in {*range(0, 13), *range(21, 33), *range(61, 73), 80}:
            return {**result, "status": "invalid_format"}
        result["checksum_valid"] = sum(int(c) * w for c, w in zip(value, (3, 7, 1) * 3)) % 10 == 0
        if not result["checksum_valid"]:
            return {**result, "status": "invalid_checksum"}
    if scheme == "BIC" and len(value) == 8:
        value += "XXX"
    result["lookup"] = {"scheme": scheme, "country": inferred_country, "value": value}
    result["validation_scope"] = "syntax_and_checksum_where_available"
    return result


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            source_id TEXT NOT NULL, snapshot_date TEXT NOT NULL,
            record_count INTEGER NOT NULL, PRIMARY KEY(source_id, snapshot_date));
        CREATE TABLE IF NOT EXISTS records (
            source_id TEXT NOT NULL, snapshot_date TEXT NOT NULL,
            record_id TEXT NOT NULL, scheme TEXT NOT NULL, country TEXT NOT NULL,
            value TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(source_id, snapshot_date, record_id));
        CREATE INDEX IF NOT EXISTS identifier_lookup ON records(scheme, country, value);
    """)
    return connection


def check_record(record):
    if not isinstance(record, dict):
        raise ValueError("A record must be an object")
    expected = {"record_id", "scheme", "country", "value", "institution", "branch", "status",
                "valid_from", "valid_to", "source", "payment_networks"}
    if set(record) != expected:
        raise ValueError("Record fields must match schema.json")
    for key in ("record_id", "scheme", "country", "value"):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError(f"{key} must be a nonempty string")
    result = validate(record["scheme"], record["value"], record["country"])
    if result["status"] != "valid_format" or record["scheme"] == "IBAN":
        raise ValueError(f"Invalid directory key: {result['status']}")
    if result["lookup"] != {k: record[k] for k in ("scheme", "country", "value")}:
        raise ValueError("Directory identifiers must be canonical (BIC11, uppercase, no separators)")
    if record["status"] not in {"active", "inactive", "unknown"}:
        raise ValueError("Invalid record status")
    if not isinstance(record["institution"], dict) or set(record["institution"]) != {"id", "name"}:
        raise ValueError("institution requires id and name")
    institution = record["institution"]
    if not isinstance(institution["id"], str) or not institution["id"].strip():
        raise ValueError("Institution id must be a nonempty string")
    if institution["name"] is not None and (not isinstance(institution["name"], str) or not institution["name"].strip()):
        raise ValueError("Institution name must be a nonempty string or null when unresolved")
    branch = record["branch"]
    if branch is not None and (not isinstance(branch, dict) or set(branch) != {"id", "name", "address"}
                               or not all(isinstance(v, str) for v in branch.values())):
        raise ValueError("branch must be null or an id/name/address object")
    source = record["source"]
    if not isinstance(source, dict) or set(source) != {"id", "url", "license", "snapshot_date", "retrieved_at"}:
        raise ValueError("Invalid source metadata")
    if not all(isinstance(v, str) and v.strip() for v in source.values()):
        raise ValueError("All source metadata fields must be nonempty strings")
    if not source["url"].startswith("https://"):
        raise ValueError("Source URL must use HTTPS")
    day(source["snapshot_date"])
    if day(source["retrieved_at"]) < day(source["snapshot_date"]):
        raise ValueError("retrieved_at must not precede snapshot_date")
    for key in ("valid_from", "valid_to"):
        if record[key] is not None:
            day(record[key])
    if record["valid_from"] and record["valid_to"] and record["valid_from"] >= record["valid_to"]:
        raise ValueError("valid_to must be later than valid_from (exclusive end)")
    if not isinstance(record["payment_networks"], list):
        raise ValueError("payment_networks must be a list")
    for network in record["payment_networks"]:
        if not isinstance(network, dict) or set(network) != {"network", "participates"}:
            raise ValueError("Invalid payment network observation")
        if not isinstance(network["network"], str) or not network["network"]:
            raise ValueError("Network name required")
        if network["participates"] is not None and type(network["participates"]) is not bool:
            raise ValueError("participates must be boolean or null")


def import_records(connection, records, expected_count=None):
    """One complete source snapshot per call. Invalid/empty input rolls back fully."""
    identity = None
    source_metadata = None
    count = 0
    with connection:
        for record in records:
            check_record(record)
            source = record["source"]
            current = (source["id"], source["snapshot_date"])
            if identity is None:
                identity = current
                source_metadata = source
                connection.execute("DELETE FROM records WHERE source_id=? AND snapshot_date=?", identity)
            elif current != identity:
                raise ValueError("Import exactly one source snapshot per file")
            elif source != source_metadata:
                raise ValueError("Source metadata must be consistent within a snapshot")
            connection.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (*identity, record["record_id"], record["scheme"], record["country"],
                                record["value"], json.dumps(record, ensure_ascii=False)))
            count += 1
        if not count:
            raise ValueError("Empty snapshots are rejected")
        if expected_count is not None and count != expected_count:
            raise ValueError(f"Expected {expected_count} records, found {count}; import rolled back")
        connection.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)", (*identity, count))
    return {"imported": count, "source_id": identity[0], "snapshot_date": identity[1]}


def jsonl_records(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}") from exc


def razorpay_records(path, manifest):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"BANK", "IFSC", "BRANCH", "ADDRESS"} <= set(reader.fieldnames or []):
            raise ValueError("Expected Razorpay IFSC CSV columns")
        for row in reader:
            name = row["BANK"].strip()
            institution_id = ("razorpay-ifsc:name:" + hashlib.sha256(name.encode()).hexdigest()[:20]
                              if name else "razorpay-ifsc:unresolved:" + row["IFSC"][:4])
            observations = []
            for network in ("NEFT", "RTGS", "IMPS", "UPI"):
                raw = (row.get(network) or "").strip().lower()
                participates = {"true": True, "false": False, "1": True, "0": False}.get(raw)
                observations.append({"network": network, "participates": participates})
            yield {
                "record_id": row["IFSC"], "scheme": "IN_IFSC", "country": "IN", "value": row["IFSC"],
                "institution": {"id": institution_id, "name": name or None},
                "branch": {"id": row["IFSC"], "name": row["BRANCH"], "address": row["ADDRESS"]},
                "status": "unknown", "valid_from": None, "valid_to": None,
                "source": {key: manifest[key] for key in ("id", "url", "license", "snapshot_date", "retrieved_at")},
                "payment_networks": observations,
            }


def lookup(connection, scheme, value, country=None, as_of=None, max_age_days=30):
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    reference_day = day(as_of)
    if max_age_days < 0:
        raise ValueError("max_age_days must be nonnegative")
    validation = validate(scheme, value, country)
    result = {"status": validation["status"], "as_of": as_of, "validation": validation, "matches": []}
    if validation["status"] != "valid_format":
        return result
    key = validation["lookup"]
    rows = connection.execute("""
        SELECT r.payload FROM records r
        JOIN (SELECT source_id, MAX(snapshot_date) AS snapshot_date FROM snapshots
              WHERE snapshot_date <= ? GROUP BY source_id) s
        ON r.source_id=s.source_id AND r.snapshot_date=s.snapshot_date
        WHERE r.scheme=? AND r.country=? AND r.value=?
        ORDER BY r.source_id, r.record_id
    """, (as_of, key["scheme"], key["country"], key["value"]))
    for row in rows:
        record = json.loads(row["payload"])
        if record["valid_from"] and day(record["valid_from"]) > reference_day:
            continue
        if record["valid_to"] and day(record["valid_to"]) <= reference_day:
            continue
        age = (reference_day - day(record["source"]["snapshot_date"])).days
        record["freshness"] = {"age_days": age, "stale": age > max_age_days, "max_age_days": max_age_days}
        result["matches"].append(record)
    count = len(result["matches"])
    result["status"] = "not_found" if not count else "matched" if count == 1 else "ambiguous"
    if count == 1 and result["matches"][0]["institution"]["name"] is None:
        result["status"] = "unresolved_institution"
    result["coverage"] = "local_imported_snapshots_only"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="directory.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "lookup"):
        command = sub.add_parser(name)
        command.add_argument("scheme")
        command.add_argument("value")
        command.add_argument("--country")
        if name == "lookup":
            command.add_argument("--as-of")
            command.add_argument("--max-age-days", type=int, default=30)
    command = sub.add_parser("import-jsonl")
    command.add_argument("path")
    command = sub.add_parser("import-razorpay")
    command.add_argument("path")
    command.add_argument("--manifest", required=True)
    sub.add_parser("stats")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            result = validate(args.scheme, args.value, args.country)
        else:
            # Avoid silently creating an empty directory for a misspelled lookup path.
            if args.command in {"lookup", "stats"} and not Path(args.db).is_file():
                raise ValueError("Database does not exist; import a snapshot first")
            connection = connect(args.db)
            try:
                if args.command == "import-jsonl":
                    result = import_records(connection, jsonl_records(args.path))
                elif args.command == "import-razorpay":
                    manifest = json.loads(Path(args.manifest).read_text())
                    digest = hashlib.sha256(Path(args.path).read_bytes()).hexdigest()
                    if digest != manifest["sha256"]:
                        raise ValueError("Input SHA-256 does not match manifest")
                    result = import_records(connection, razorpay_records(args.path, manifest), manifest["record_count"])
                elif args.command == "stats":
                    result = {"snapshots": [dict(r) for r in connection.execute("SELECT * FROM snapshots ORDER BY source_id, snapshot_date")]}
                else:
                    result = lookup(connection, args.scheme, args.value, args.country, args.as_of, args.max_age_days)
            finally:
                connection.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
