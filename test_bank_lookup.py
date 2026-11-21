import json
import sqlite3
import unittest
from bank_lookup import connect, import_records, lookup, validate, check_record


def record(value="021000021", source="test", snapshot="2026-11-01", name="Fixture institution"):
    return {"record_id": value, "scheme": "US_ABA", "country": "US", "value": value,
            "institution": {"id": "fixture-only", "name": name}, "branch": None,
            "status": "unknown", "valid_from": None, "valid_to": None,
            "source": {"id": source, "url": "https://example.com/fixtures", "license": "Synthetic test fixture",
                       "snapshot_date": snapshot, "retrieved_at": "2026-11-21"}, "payment_networks": []}


class ValidationTests(unittest.TestCase):
    def test_aba_checksum_and_prefix(self):
        self.assertEqual(validate("US_ABA", "021000021")["status"], "valid_format")
        self.assertEqual(validate("US_ABA", "021000022")["status"], "invalid_checksum")
        self.assertEqual(validate("US_ABA", "991000000")["status"], "invalid_format")

    def test_exact_normalization(self):
        self.assertEqual(validate("GB_SORT_CODE", "12-34-56")["lookup"]["value"], "123456")
        self.assertEqual(validate("AU_BSB", "123-456")["lookup"]["value"], "123456")
        for scheme, value in [("US_ABA", "021-000-021"), ("GB_SORT_CODE", "123-456"),
                              ("IN_IFSC", "ＳＢＩＮ0000001"), ("BIC", "ABCDUS3!")]:
            with self.subTest(scheme=scheme, value=value):
                self.assertEqual(validate(scheme, value)["status"], "invalid_format")

    def test_bic_default_branch_only(self):
        self.assertEqual(validate("BIC", "chasus33")["lookup"]["value"], "CHASUS33XXX")
        self.assertEqual(validate("BIC", "CHASUS33ABC")["lookup"]["value"], "CHASUS33ABC")
        self.assertEqual(validate("BIC", "CHASUS33", "GB")["status"], "country_mismatch")

    def test_national_formats(self):
        for scheme, value in [("IN_IFSC", "SBIN0000001"), ("CA_EFT", "000300001"),
                              ("DE_BLZ", "37040044")]:
            self.assertEqual(validate(scheme, value)["status"], "valid_format")
        self.assertEqual(validate("IN_IFSC", "SBIN1000001")["status"], "invalid_format")
        self.assertEqual(validate("CA_EFT", "100300001")["status"], "invalid_format")

    def test_iban_extracts_without_echoing_account(self):
        cases = [
            ("DE89370400440532013000", "DE_BLZ", "37040044"),
            ("GB82WEST12345698765432", "GB_SORT_CODE", "123456"),
            ("NL91ABNA0417164300", "NL_BANK_CODE", "ABNA"),
            ("FR1420041010050500013M02606", "FR_BANK_BRANCH", "2004101005"),
            ("ES9121000418450200051332", "ES_BANK_BRANCH", "21000418"),
            ("IT60X0542811101000000123456", "IT_BANK_BRANCH", "0542811101"),
        ]
        for value, scheme, key in cases:
            with self.subTest(country=value[:2]):
                parsed = validate("IBAN", value)
                self.assertEqual(parsed["status"], "valid_format")
                self.assertEqual(parsed["lookup"]["scheme"], scheme)
                self.assertEqual(parsed["lookup"]["value"], key)
                self.assertNotIn(value, json.dumps(parsed))
        self.assertEqual(validate("IBAN", "DE88370400440532013000")["status"], "invalid_checksum")
        self.assertEqual(validate("IBAN", "DE89 3704 0044 0532 0130 00")["status"], "valid_format")
        self.assertEqual(validate("IBAN", "DE89370400440532013000", "GB")["status"], "country_mismatch")
        self.assertEqual(validate("IBAN", "BE68539007547034")["status"], "unsupported_country")
        self.assertEqual(validate("OTHER", "123")["status"], "unsupported_scheme")


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect(":memory:")

    def tearDown(self):
        self.connection.close()

    def test_lookup_freshness_and_missing(self):
        import_records(self.connection, [record(snapshot="2026-08-15")])
        result = lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-21")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matches"][0]["freshness"]["age_days"], 98)
        self.assertTrue(result["matches"][0]["freshness"]["stale"])
        self.assertEqual(lookup(self.connection, "US_ABA", "011000015")["status"], "not_found")
        self.assertEqual(lookup(self.connection, "US_ABA", "021000022")["status"], "invalid_checksum")

    def test_snapshots_as_of_and_no_fallback_to_deleted_records(self):
        import_records(self.connection, [record(snapshot="2026-10-01", name="Earlier name")])
        import_records(self.connection, [record(snapshot="2026-11-01", name="Later name")])
        previous = lookup(self.connection, "US_ABA", "021000021", as_of="2026-10-21")
        self.assertEqual(previous["matches"][0]["institution"]["name"], "Earlier name")
        current = lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-21")
        self.assertEqual(current["matches"][0]["institution"]["name"], "Later name")
        import_records(self.connection, [record(value="011000015", snapshot="2026-11-15")])
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-21")["status"], "not_found")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 3)

    def test_atomic_rollback_and_empty_rejection(self):
        original = record()
        import_records(self.connection, [original])
        invalid = record("invalid")
        for batch, expected in [([record(name="New"), invalid], None), ([], None),
                                ([original, original], None), ([record(name="New")], 2)]:
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                import_records(self.connection, batch, expected)
            result = lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-21")
            self.assertEqual(result["matches"][0]["institution"]["name"], "Fixture institution")

    def test_ambiguity_is_not_silently_resolved(self):
        import_records(self.connection, [record(source="one")])
        import_records(self.connection, [record(source="two", name="Conflicting institution")])
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021")["status"], "ambiguous")

    def test_unknown_name_is_not_reported_as_identified(self):
        import_records(self.connection, [record(name=None)])
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021")["status"], "unresolved_institution")

    def test_effective_interval(self):
        r = record()
        r.update(valid_from="2026-11-05", valid_to="2026-11-20")
        import_records(self.connection, [r])
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-04")["status"], "not_found")
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-19")["status"], "matched")
        self.assertEqual(lookup(self.connection, "US_ABA", "021000021", as_of="2026-11-20")["status"], "not_found")

    def test_bic_branch_is_not_guessed(self):
        r = record()
        r.update(scheme="BIC", value="CHASUS33XXX", record_id="bic")
        import_records(self.connection, [r])
        self.assertEqual(lookup(self.connection, "BIC", "CHASUS33")["status"], "matched")
        self.assertEqual(lookup(self.connection, "BIC", "CHASUS33ABC")["status"], "not_found")

    def test_import_validates_strings(self):
        r = record()
        r["value"] = 21000021
        with self.assertRaises(ValueError):
            check_record(r)

    def test_snapshot_provenance_must_agree(self):
        first, second = record(), record("011000015")
        second["source"]["url"] = "https://example.com/another-feed"
        with self.assertRaises(ValueError):
            import_records(self.connection, [first, second])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM records").fetchone()[0], 0)

    def test_invalid_dates_and_network_flags_rejected(self):
        for date in ["20261101", "2026-02-30", "2026-W45-1"]:
            r = record(snapshot=date)
            with self.assertRaises(ValueError):
                check_record(r)
        r = record()
        r["payment_networks"] = [{"network": "ACH", "participates": "true"}]
        with self.assertRaises(ValueError):
            check_record(r)


if __name__ == "__main__":
    unittest.main()
