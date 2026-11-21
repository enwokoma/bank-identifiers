# Bank Identifier Directory — starter collection and lookup engine

An offline foundation for resolving financial institution identifiers to source-backed institution and branch observations. Prepared November 21, 2026.

Public repository: [enwokoma/bank-identifiers](https://github.com/enwokoma/bank-identifiers).

**This is a starter, not a complete worldwide bank directory.** It includes an actual Indian IFSC snapshot, a researched global source catalog, and extensible lookup code. US routing numbers, global BICs, and other national directories still need suitable data feeds.

## Included collection

| Item | Contents |
|---|---|
| Real data | 183,214 IFSC rows from Razorpay release v2.0.62 |
| Named institution records | 182,179 |
| Unresolved institution names | 1,035 rows have a blank upstream `BANK` field; preserved as null |
| Upstream snapshot date | August 15, 2026, from the release's RBI Update Date |
| Upstream release publication | September 1, 2026 |
| Retrieval date | November 21, 2026 |
| Freshness on preparation date | 98 days old; stale under the default 30-day threshold |
| Other directory records | None included |

The dataset is a third-party compilation in an MIT-licensed repository, not a certification of all current Indian IFSC assignments. The upstream license notice is included in `licenses/RAZORPAY-MIT.txt`. Record counts describe this pinned file, rather than current nationwide completeness. The source catalog distinguishes public access, paid subscriptions, and redistribution rights.

## Quick start

Requires Python 3.10 or later. No third-party Python packages or credentials are needed. Clone the repository, then run these commands from its folder:

```sh
git clone https://github.com/enwokoma/bank-identifiers.git
cd bank-identifiers
```

```sh
python3 bank_lookup.py import-razorpay data/ifsc-v2.0.62.csv.gz --manifest data/ifsc-v2.0.62.manifest.json
python3 bank_lookup.py lookup IN_IFSC SBIN0000001 --country IN --as-of 2026-11-21
python3 bank_lookup.py stats
python3 -m unittest -v test_bank_lookup.py
```

The first command checks the compressed file's SHA-256 and expected row count, then builds `directory.sqlite`. The example lookup returns **State Bank of India / KOLKATA MAIN**, with snapshot date, provenance, unknown allocation status, observed payment-network flags, and `stale: true`. Network flags are observations from the dated source, not live reachability checks.

An unresolved example:

```sh
python3 bank_lookup.py lookup IN_IFSC AANB0000001 --as-of 2026-11-21
```

This returns `unresolved_institution`, with the known branch/address but no invented bank name.

## Format validation and IBAN parsing

```sh
python3 bank_lookup.py validate US_ABA 021000021 --country US
python3 bank_lookup.py validate BIC CHASUS33
python3 bank_lookup.py validate IBAN 'GB82 WEST 1234 5698 7654 32'
```

Validation works before a database is imported. A `valid_format` result means syntax/checksum checks passed; it does not establish that a code is allocated, current, or belongs to a particular institution. The last command extracts sort code `123456` and bank code `WEST`; it does not echo the account number.

| Scheme | Accepted input / normalization |
|---|---|
| `US_ABA` | Nine digits; ABA prefix and weighted checksum checks; leading zeros retained |
| `BIC` | BIC8 or BIC11; uppercase; BIC8 becomes BIC11 ending `XXX` |
| `GB_SORT_CODE` | Six digits or `12-34-56` |
| `DE_BLZ` | Eight digits |
| `AU_BSB` | Six digits or `123-456` |
| `IN_IFSC` | Four letters, literal zero, six alphanumeric characters |
| `CA_EFT` | Nine-digit electronic routing format beginning with zero; paper format is not implemented |
| `NL_BANK_CODE` | Four letters extracted from an IBAN |
| `FR_BANK_BRANCH` | Five-digit bank code followed by five-digit branch code |
| `ES_BANK_BRANCH` | Four-digit bank code followed by four-digit branch code |
| `IT_BANK_BRANCH` | Five-digit ABI followed by five-digit CAB |
| `IBAN` | National structure and MOD97 check; extract a directory key |

`iban_rules.json` currently covers **DE, GB, NL, FR, ES, IT**. Other countries return `unsupported_country`, which does not mean their IBAN is invalid. IBAN parsing support and institution mapping coverage are separate. FR, ES and IT require an exact combined bank/branch directory key; there is no fallback to bank-only mappings. National account checksum algorithms beyond the IBAN checksum are not implemented.

The BIC validator implements the general ISO 9362 alphanumeric party-prefix structure. Swift messaging has a narrower alphabetic party-prefix convention. Neither syntactic check proves country-code allocation, BIC registration, financial-institution status, or connectivity. A named non-default BIC11 branch never falls back to a BIC8/default branch.

## Importing additional data

Convert each licensed source snapshot to JSON Lines: one record matching `schema.json` per line. Then:

```sh
python3 bank_lookup.py import-jsonl path/to/complete-source-snapshot.jsonl
```

Each import must contain **one complete source snapshot**, identified by `source.id` and `source.snapshot_date`. Do not use a small incremental change file as a complete snapshot. New dates retain earlier snapshots. Reimporting the same source/date replaces that snapshot atomically. Invalid records, duplicate record IDs, empty input, inconsistent source metadata, or Razorpay checksum/count failures roll back the import.

All identifiers are strings. Canonical stored BICs have 11 characters. Full IBANs and account numbers cannot be stored as directory keys. The schema documents an exchange format; the importer also enforces scheme syntax, canonical values, source consistency and date ordering. If using a separate JSON Schema validator, enable `format` validation for dates and URIs.

Example shape (illustrative placeholder, not an actual bank mapping):

```json
{
  "record_id": "your-source-record-id",
  "scheme": "US_ABA",
  "country": "US",
  "value": "021000021",
  "institution": {"id": "your-stable-institution-id", "name": "Replace with sourced name"},
  "branch": null,
  "status": "unknown",
  "valid_from": null,
  "valid_to": null,
  "source": {
    "id": "your-licensed-source",
    "url": "https://example.com/your-feed",
    "license": "Your actual contract or license reference",
    "snapshot_date": "2026-11-01",
    "retrieved_at": "2026-11-21"
  },
  "payment_networks": []
}
```

`valid_to` is an exclusive end date. Null effective dates mean unknown. Lookup chooses the most recent imported snapshot dated on or before `--as-of` for each source, then applies explicit validity intervals. It never resurrects an identifier removed from the newest eligible snapshot. An `--as-of` query represents available source observations, not reconstructed legal truth or knowledge of future data.

Razorpay institution IDs in this starter are source-scoped hashes of supplied names. Rows with missing names use a source-prefix grouping and remain unresolved. These are provisional grouping keys, not global legal-entity identifiers. No mergers, parents, aliases, successor banks, fintech brands, sponsor banks or cross-source identities are guessed. A routing institution can differ from a customer's app/brand or sponsoring arrangement.

## Results and integration

| Status | Meaning |
|---|---|
| `matched` | One directory observation was found; inspect source age and allocation status |
| `unresolved_institution` | One observation found, but institution name is missing |
| `ambiguous` | Multiple observations; all are returned without selecting a winner |
| `not_found` | No mapping in eligible local snapshots; not proof an identifier is invalid |
| `invalid_format` | Syntax or national structure failed |
| `invalid_checksum` | ABA or IBAN checksum failed |
| `country_mismatch` | Supplied country disagrees with the scheme or embedded country |
| `unsupported_country` | IBAN country has no installed parsing rule |
| `unsupported_scheme` | No validator for this scheme |

Usage errors print a JSON error on stderr and exit 2; ordinary lookup outcomes return JSON on stdout and exit 0. The CLI uses the current UTC date unless `--as-of YYYY-MM-DD` is supplied. Pass an explicit business date when your application uses another timezone. `--max-age-days` changes the reporting threshold without hiding older matches.

Use directly from Python with `connect(database_path)` and `lookup(connection, scheme, value, country, as_of, max_age_days)`. An HTTP service, authentication, distributed updates and deployment are outside this starter. No lookup sends identifiers to a third-party service. If accepting full IBANs in your application, avoid retaining them in request logs or analytics. The command line itself can retain arguments in shell history.

## Files and publication

- `GIST.md`: concise, publishable research/specification draft with primary-source links.
- `sources.csv` / `sources.json`: 11 candidate sources, access conditions and usage-rights notes.
- `schema.json`: normalized observation schema.
- `coverage.json`: actual included data and remaining gaps.
- `iban_rules.json`: six explicit parsing rules, independently extendable.
- `bank_lookup.py`: standard-library CLI and importable lookup functions.
- `test_bank_lookup.py`: validation, rollback, historical lookup and ambiguity tests.
- `data/`: compressed IFSC snapshot and hash/count/date manifest.
- `licenses/`: original upstream license notice for the included collection.

For a separate GitHub Gist, use `GIST.md`, `sources.csv`, `sources.json`, `schema.json`, `coverage.json`, `iban_rules.json`, and the Python files. The public repository includes the compressed data snapshot with its provenance and license notice.

The bundle has no blanket license over third-party directory data. Before adding a feed, record rights for internal storage, API disclosure, caching and bulk redistribution. The Federal Reserve public directory restricts commercial gain; Swift data redistribution requires the appropriate agreement. Public availability does not establish permission to republish.

## Expansion order

1. Obtain an appropriately licensed US RTN feed from the official registrar or an authorized vendor; populate separate ACH and wire observations.
2. Evaluate SwiftRef Identifiers Directory for BIC and national-code mappings; confirm API disclosure and redistribution rights.
3. Add country adapters for EISCD, Bundesbank, AusPayNet and Payments Canada according to their terms and effective dates.
4. Expand `iban_rules.json` from maintained national specifications; acquire matching bank-code mappings separately.
5. Add source refresh jobs, age thresholds per feed, change reports, and reviewed cross-source identity/merger mappings. These jobs are not scheduled in this starter.

Source links and access notes are in `sources.json`. The pinned IFSC release is https://github.com/razorpay/ifsc/releases/tag/v2.0.62; national IBAN formats are maintained by Swift at https://www.swift.com/standards/data-standards/iban-international-bank-account-number.
