# Bank identifiers → financial institution lookup

Build a reference-data system that resolves routing and clearing identifiers to source-backed institution and branch observations. Identifier validation, directory allocation, and payment reachability are separate results.

Prepared November 21, 2026. This draft and its companion starter do **not** claim complete worldwide coverage.

## Identifiers and sources

| Identifier | Lookup approach | Primary source candidate |
|---|---|---|
| US ABA / routing transit number | Exact nine-digit lookup; preserve leading zeros; separate ACH and wire participation | [ABA / official registrar](https://www.aba.com/about-us/routing-number) |
| US payment-network participation | FedACH and Fedwire directory observations | [Federal Reserve directory](https://www.frbservices.org/resources/e-payments-routing-directory/) |
| SWIFT/BIC | BIC8/BIC11 to registered entity, branch and connectivity data | [SwiftRef](https://www.swift.com/products/swiftref) |
| IBAN | Country-specific parsing → embedded national bank/branch code → institution directory | [Swift IBAN Registry](https://www.swift.com/standards/data-standards/iban-international-bank-account-number) for formats; SwiftRef/national sources for mappings |
| UK sort code | Sort code and clearing participation | [Pay.UK EISCD](https://www.wearepay.uk/what-we-do/industry-services/extended-industry-sort-code-directory/) |
| German BLZ | National bank code and effective-date changes | [Bundesbank](https://www.bundesbank.de/en/tasks/payment-systems/services/bank-sort-codes/download-bank-sort-codes-626218) |
| Australian BSB | Bank/branch code | [AusPayNet](https://bsb.auspaynet.com.au/) |
| Indian IFSC | Bank/branch identifier | [RBI](https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=2009); [Razorpay compilation](https://github.com/razorpay/ifsc) as a third-party source |
| Canadian electronic routing number | Institution and transit identifiers in electronic format | [Payments Canada FIF](https://www.payments.ca/systems-services/payment-services/financial-institutions-file) |

An IBAN identifies an **account**, so the directory should contain IBAN parsing rules and embedded bank-code mappings, not lists of customer IBANs. A BIC can identify a non-financial entity, and registration alone does not prove Swift connectivity. See [Swift's BIC definition](https://www.swift.com/standards/data-standards/bic-business-identifier-code).

## Data model

```text
Institution: stable internal ID, sourced names, country, reviewed parent links
Branch: internal ID, institution relationship, sourced name/address
Identifier: scheme, country, string value, institution/branch relationship
Observation: source, source record ID, snapshot date, retrieval date, status
Validity: known effective start/end dates, explicitly unknown when unavailable
Participation: network, observed participation, observation date
Source: publisher, URL, license, storage/disclosure/redistribution rights
```

Support multiple identifiers per institution, multiple source observations per identifier, and historical changes. Do not merge entities solely because their names or code prefixes match. Sponsor institutions and customer-facing brands can differ.

## Resolution contract

```text
Explicit scheme + optional country + value
    → normalize only permitted formatting
    → validate syntax and applicable checksums
    → for IBAN: extract the national lookup key
    → search the latest eligible snapshot for each source
    → filter known effective-date intervals
    → return all matching observations, source dates and freshness
```

Use explicit schemes because national code lengths overlap. Exact matching is appropriate; fuzzy bank-name matching is a separate identity-resolution task. Canonicalize a BIC8 to its `XXX` BIC11 representation; do not use a parent/default branch to answer an unknown named BIC11 branch.

Return distinct statuses for `invalid_format`, `invalid_checksum`, `unsupported_scheme`, `unsupported_country`, `country_mismatch`, `not_found`, `matched`, `unresolved_institution`, and `ambiguous`. A missing entry in a partial directory is not proof that a code is invalid. Surface stale matches rather than silently presenting them as current.

The system identifies a directory institution; it does not establish account ownership, beneficiary identity, account existence, or payment acceptance.

## Collection and licensing

The [Federal Reserve terms](https://www.frbservices.org/terms-of-use/) restrict commercial gain from its E-Payments Routing Directory. Swift explicitly provides a separate route for [redistribution licensing](https://www.swift.com/myswift/ordering/order-products-services/swiftref-directory-files). Treat publicly searchable or downloadable material as a candidate source until the necessary usage rights are established.

Publish the specification, source catalog, exchange schema and original code independently of restricted data. Maintain per-source provenance and license notices. Source catalog fields should include country/scheme coverage, access method, refresh cadence, usage rights and known gaps.

## Companion starter: actual coverage

The local bundle contains:

- A pinned [Razorpay v2.0.62](https://github.com/razorpay/ifsc/releases/tag/v2.0.62) IFSC CSV: **183,214 records**, of which **182,179 have institution names** and **1,035 are unresolved**.
- The upstream MIT license notice, exact input hashes, release date, and source snapshot date.
- An offline SQLite importer and lookup CLI using only Python's standard library.
- Validators for US ABA, BIC, UK sort code, German BLZ, Australian BSB, Indian IFSC, Canadian electronic routing format, and selected IBAN bank/branch keys.
- IBAN parsing for **DE, GB, NL, FR, ES and IT**; institution mappings for those keys still require feeds.
- Source catalogs, JSON Schema, coverage manifest and tests.

The IFSC release was published September 1, 2026 and reports an RBI update date of August 15, 2026. It is **98 days old as of November 21, 2026**. It is a dated third-party compilation, not a current exhaustive official directory. No bulk US, Swift or EISCD records are included.

## Path to production

1. Secure feed access and rights for the intended countries, API responses and distribution model.
2. Add adapters with pinned versions, hashes, source dates, expected counts and atomic imports.
3. Retain raw snapshots and inspect additions, removals, successor identifiers and name changes before promoting updates.
4. Add per-source freshness limits and monitoring for failed or partial refreshes.
5. Introduce reviewed institution identities, historical mergers and sponsor relationships.
6. Provide an API over the directory with privacy-aware logging and clear coverage/freshness fields.

The starter's code is a foundation; live refresh, a hosted API and full global reference-data coverage remain separate implementation work.
