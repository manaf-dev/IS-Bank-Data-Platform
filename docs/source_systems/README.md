# Source systems

Every IS Bank source system is simulated by `generator/`. One deterministic world (`generator/world/`) is projected into nine systems (`generator/projectors/`), each with its own IDs, formats, cadence, and deliberate flaws. All rates live in `generator/config.yaml` under `projectors:`. Every flaw is decided by a stable hash of (seed, record), so reruns and daily ticks reproduce the same mess exactly.

The ground truth for every flaw is in the answer key (`answer_key/<profile>/`), for offline evaluation only.

## Landing layout

`python -m generator backfill --out <dir>` writes `<dir>/landing/`, mirroring `lh_bronze/Files`:

```
landing/<system>/<YYYY>/<MM>/<DD>/<file>      daily, weekly, and monthly deliveries
landing/core_banking/full_<date>/<table>.parquet   initial relational load
landing/core_banking/cdc_<date>/<table>.parquet    daily upserts (one folder per tick)
```

CRM files go to `--crm-dir` or `$ISBANK_CRM_DRIVE_DIR` (a Google Drive for desktop folder, ADR-0004).

## Systems

| System | Format & cadence | Identifies customers by | Planted flaws |
|---|---|---|---|
| **Core banking** | Relational (Parquet staging for the Fabric SQL database); full load + daily CDC | CIF number (`CIF########`), 13-digit account numbers | Same person with two CIFs at different branches (often a name typo), some later accounts on the duplicate; missing email / digital address / Ghana Card; placeholder DOB `1900-01-01`; rare double-posted transactions |
| **Card switch** | JSON lines, filed by delivery day | Masked PAN + card token only | Declined authorisations (response `51`); late events (minutes to 3 days); duplicate deliveries |
| **Mobile banking** | JSON lines, filed by delivery day | App user id (`MB#######`), device id | Late events; app 3.0 adds `biometric_used` from 2024-06-01 (schema drift); takeover sessions start with failed logins from unknown devices (but genuine users fail logins and add payees too) |
| **Loan origination (LOS)** | Daily CSV extracts | Ghana Card in mixed formats, `SURNAME First` names | Amounts as formatted strings (`"3,500.00"`); PENDING then decision rows for the same application; `monthly_income` renamed `net_monthly_income` from 2024-07-01; files occasionally delivered twice |
| **CRM** | Daily CSV exports (Google Drive) | Contact id, name, email, phone (no CIF, no Ghana Card) | Name typos; many missing emails; mixed phone formats; `preferred_channel` column added from 2024-03-01 |
| **General ledger** | Monthly Excel workbook, human-formatted | Branch cost centre | Title rows, pivoted layout, totals, footnotes; deliberate small fee-income breaks vs the core |
| **Credit bureau** | Weekly fixed-width text (Mondays) with header/trailer | National ID digits, surname, first name, DOB | Names truncated to field width; score is predictive but noisy |
| **Compliance lists** | Monthly CSV snapshots | Name, DOB, sometimes ID | Customer PEPs listed under name variants; synthetic entries that share common customer names (false hits) |
| **Reference data** | Daily CSV (FX) | n/a | Synthetic GHS FX random walk. Ghana holidays and macro indicators come from public APIs (Nager.Date, World Bank) and are not yet fetched |

## Planted business signals

Visible across the sources, never labelled in them:

- **Fraud:** card testing, night-time purchases in another city, account-takeover rings sharing payee accounts and devices (about 0.06% of postings at full scale).
- **Credit default:** true probability from DTI, macro stress, income, and segment plus noise. Defaulters suffer an income shock early in the loan, and missed repayments follow.
- **Churn:** a hazard raised by unresolved complaints and low activity. Churners run down for 2-6 months, move their money out, then go silent. NPS drops beforehand.
- **Branch economics:** treasury income on deposits, loan interest, and fees against customer-scaled opex, so some branches are profitable and some are not.
