# Design – Retail Fabric Lakehouse

Status: **Approved** · Phase 1 · Owner: Akshay

---

## 1. Business context

A retailer sells through physical stores, a website and a mobile app. Management wants a
daily-refreshed sales model to answer:

| # | Business question | KPI / measure |
|---|---|---|
| Q1 | How much did we sell yesterday / this month? | Net sales, orders, units |
| Q2 | Which categories and products drive revenue and margin? | Net sales, gross margin % by category / product |
| Q3 | How do channels compare (store vs online vs mobile)? | Net sales, average order value (AOV) by channel |
| Q4 | How many customers buy, and which segments / loyalty tiers? | Active customers, sales by segment & tier |
| Q5 | How much is cancelled or returned? | Cancelled / returned order count and booked amount |

**Freshness SLA:** data up to end of previous day (D-1) available by **07:00 UTC**.

### Scope
- In: 4 source entities, bronze/silver/gold layers, data quality, logging, unit tests, CI, dev→prod deployment.
- Out (non-goals): real-time streaming, inventory, stores dimension, PII masking, row-level security.

---

## 2. Source system (simulated)

Because no external systems are available, `nb_01_generate_source` simulates the retailer's
OLTP system. Each run it produces **one daily extract per entity** and writes it to OneLake.
The pipeline must treat these files exactly as if they came from an external system
(it never reads the generator's internal state).

### 2.1 Delivery contract

| Property | Value |
|---|---|
| Location | `lh_retail/Files/landing/<entity>/<yyyy-mm-dd>/<entity>_<yyyymmdd>.<ext>` |
| Frequency | Daily, one folder per business date |
| Extract type | **Incremental (change data)**: rows created or changed that day; first day is a full initial load |
| Formats | CSV (header, comma, UTF-8) for customers, orders, order_items; **JSON lines** for products |
| Timestamps | `yyyy-MM-dd HH:mm:ss`, UTC |
| Change detection | `updated_at` column on every entity: the latest `updated_at` per key wins |
| Deletes | Not sent (soft status only, e.g. `is_active`, `order_status`) |

### 2.2 Entities

**customers** (CSV), key `customer_id`

| Column | Type | Notes |
|---|---|---|
| customer_id | int | PK |
| first_name, last_name | string | |
| email | string | may contain upper case / spaces |
| phone | string | |
| city, state, country | string | can change over time (moves) |
| segment | string | CONSUMER, CORPORATE, SMALL_BUSINESS |
| loyalty_tier | string | BRONZE, SILVER, GOLD, PLATINUM; changes over time |
| signup_date | date | |
| updated_at | timestamp | |

**products** (JSON lines), key `product_id`

| Column | Type | Notes |
|---|---|---|
| product_id | int | PK |
| sku | string | |
| product_name, category, subcategory, brand | string | |
| unit_price, unit_cost | decimal | price changes over time |
| attributes | struct{color, size} | nested, flattened in silver |
| is_active | boolean | |
| updated_at | timestamp | |

**orders** (CSV), key `order_id`

| Column | Type | Notes |
|---|---|---|
| order_id | long | PK |
| customer_id | int | **null = guest checkout** |
| order_ts | timestamp | when the order was placed |
| channel | string | STORE, ONLINE, MOBILE |
| payment_method | string | CARD, CASH, WALLET, GIFT_CARD |
| order_status | string | PLACED → SHIPPED → DELIVERED (online); COMPLETED (store); CANCELLED; RETURNED |
| updated_at | timestamp | a status change re-sends the whole order row |

**order_items** (CSV), key `order_line_id`

| Column | Type | Notes |
|---|---|---|
| order_line_id | string | PK, `<order_id>-<line_no>` |
| order_id | long | FK → orders |
| line_no | int | |
| product_id | int | FK → products |
| quantity | int | |
| unit_price | decimal | price at time of sale |
| discount_pct | decimal | 0.00–0.50 |
| updated_at | timestamp | |

### 2.3 Known data problems (deliberately injected, ~1% of rows)

| Problem | Entity | How the pipeline must handle it |
|---|---|---|
| Exact duplicate rows | all | Deduplicate in silver |
| Same key sent twice with different `updated_at` | customers, orders | Latest `updated_at` wins |
| Late / out-of-order update (older `updated_at` arrives after newer) | orders | Must **not** overwrite newer data |
| Unparseable `order_ts` | orders | Quarantine |
| Guest orders (null customer_id) | orders | Load; map to Unknown customer in gold |
| Quantity ≤ 0, negative price | order_items | Quarantine |
| Orphan product_id (not in products) | order_items | **Quarantine**; retried automatically on later runs in case the product arrives late |
| Late-arriving order header (items arrive a day before the order) | orders/items | Load items; fix gold automatically when the header arrives |
| Messy text (case, spaces) | customers | Standardise in silver |
| Malformed record (bad JSON / wrong column count) | all | Quarantine |

---

## 3. Architecture

```
nb_01_generate_source ─► Files/landing ─► nb_02_bronze ─► bronze.*
                                                   │
                         nb_03_silver ◄────────────┘ ─► silver.*  (+ silver.quarantine_*)
                                                   │
                         nb_04_gold   ◄────────────┘ ─► gold.*
                                                   │
                         nb_05_validate ◄──────────┘ ─► ops.dq_results (fail pipeline on error)

All steps write ops.run_log; orchestrated by pl_retail_daily.
```

One lakehouse `lh_retail` with schemas **bronze, silver, gold, ops**. The same name is used in
dev and prod workspaces, so code needs no environment-specific paths.

### 3.1 Layer responsibilities

| Layer | Contains | Write mode | Rules |
|---|---|---|---|
| **landing** (Files) | source files as delivered | written by source | never modified by the pipeline |
| **bronze** | raw rows, **all columns as string**, + metadata | append-only | no business logic; replayable history |
| **silver** | typed, cleaned, deduplicated, one row per key (current state) | MERGE (upsert) | DQ rules applied; rejects → quarantine |
| **gold** | star schema for reporting | MERGE / partial overwrite | surrogate keys, unknown members, business measures |
| **ops** | logs, watermarks, DQ results | append / MERGE | operational metadata only |

### 3.2 Technical columns

| Column | Layer | Meaning |
|---|---|---|
| `_source_file` | bronze | full path of the landing file |
| `_batch_date` | bronze | business date from the landing folder name |
| `_ingest_ts` | bronze | when the row was loaded to bronze |
| `_run_id` | bronze, silver | run that loaded/changed the row |
| `_silver_updated_ts` | silver | when the row was last inserted/updated in silver |
| `_dq_errors`, `_quarantined_ts` | quarantine | failed rules and when |

---

## 4. Incremental load strategy

Every step is **idempotent**: running it twice for the same input gives the same result.

| Step | Picks up new data by | Stored in |
|---|---|---|
| landing → bronze | file paths not yet in `ops.file_log` | `ops.file_log` (one row per file loaded) |
| bronze → silver | `_ingest_ts` > last watermark per entity | `ops.watermark` |
| silver → gold | `_silver_updated_ts` > last watermark per source table | `ops.watermark` |

- **Watermarks are advanced only after a successful write.** A failed run is simply re-run.
- **Silver MERGE rule:** update only when `source.updated_at > target.updated_at` (protects against late, older records).
- **Gold fact refresh:** recompute fact rows for every `order_id` touched in the increment
  (new items **or** changed order header), then MERGE on `order_line_id`. This fixes
  late-arriving headers and status changes automatically.
- **Backfill / reprocess:** reset the watermark (or delete `ops.file_log` rows) for the entity and re-run.

---

## 5. Silver model

| Table | Key | Notes |
|---|---|---|
| silver.customers | customer_id | trimmed, `email` lower-cased, names Initcap |
| silver.products | product_id | `attributes` flattened to `color`, `size` |
| silver.orders | order_id | `order_date` derived from `order_ts` |
| silver.order_items | order_line_id | `line_amount` = quantity × unit_price × (1 − discount_pct) |
| silver.quarantine_<entity> | – | raw record (`_raw_json`) + `_dq_errors` + `_run_id` |

---

## 6. Gold model (star schema)

```
                 gold.dim_date
                      │ date_key
gold.dim_customer ── gold.fact_sales ── gold.dim_product
   customer_sk                              product_sk

gold.agg_sales_daily  (date × category × channel)
```

### 6.1 Facts

**gold.fact_sales**: **grain: one row per order line**

| Column | Description |
|---|---|
| order_line_id (BK), order_id | business keys |
| date_key | `yyyymmdd` of order_ts; `-1` if order header missing |
| customer_sk, product_sk | FK to dims; `-1` = Unknown |
| channel, payment_method, order_status | degenerate dimensions |
| quantity | units |
| gross_amount | quantity × unit_price |
| discount_amount | gross_amount × discount_pct |
| booked_amount | gross_amount − discount_amount (value when ordered, always populated) |
| is_cancelled, is_returned | flags from order_status |
| is_revenue | `true` unless order_status is CANCELLED or RETURNED |
| net_amount | booked_amount if is_revenue else 0 |
| cost_amount | quantity × product unit_cost if is_revenue else 0 |
| margin_amount | net_amount − cost_amount |
| _gold_updated_ts | load timestamp |

**Cancelled / returned orders** are kept in the fact (never physically deleted) but contribute **0** to
net sales, cost and margin. Their value stays visible in `booked_amount` for the Q5 return/cancel KPIs.
When an order changes status to RETURNED, the fact rows are updated by the normal incremental refresh.

**gold.agg_sales_daily**: grain: date_key × category × channel; orders, units, net_amount, margin_amount
(revenue orders only), plus cancelled_amount and returned_amount.
Rebuilt only for the dates touched in each run.

### 6.2 Dimensions

| Dimension | SCD type | Business key | Surrogate key | Tracked history |
|---|---|---|---|---|
| dim_customer | **Type 2** | customer_id | `customer_sk` | city, state, segment, loyalty_tier → new version; other columns overwrite current version |
| dim_product | Type 1 | product_id | `product_sk` | none (overwrite) |
| dim_date | static | date | `date_key` (yyyymmdd) | generated 2020-01-01 … 2030-12-31 |

- **Surrogate keys:** `xxhash64` of business key (+ `valid_from` for SCD2): deterministic, no sequence needed.
- **SCD2 columns:** `valid_from`, `valid_to` (`9999-12-31` for current), `is_current`.
  First version of every customer gets `valid_from = 1900-01-01` so historical orders always match.
- **Point-in-time lookup:** fact picks the customer version where `valid_from ≤ order_ts < valid_to`.
- **Unknown member:** every dim has a `-1` row (`Unknown`) so facts never drop rows or have null keys.

---

## 7. Data quality rules

Severity **error** → row quarantined (not loaded). **warn** → row loaded, failure counted.

| Entity | Rule | Check (valid when TRUE) | Severity |
|---|---|---|---|
| all | not_malformed | `_corrupt_record IS NULL` | error |
| all | key_not_null | primary key IS NOT NULL | error |
| all | updated_at_valid | `updated_at` parses as timestamp | error |
| customers | email_format | email matches `^[^@\s]+@[^@\s]+\.[^@\s]+$` | warn |
| customers | segment_valid | segment IN (allowed values) | warn |
| products | price_positive | unit_price > 0 | error |
| products | cost_le_price | unit_cost ≤ unit_price | warn |
| orders | order_ts_valid | `order_ts` parses as timestamp | error |
| orders | status_valid | order_status IN (allowed values) | error |
| orders | customer_present | customer_id IS NOT NULL | warn (guest) |
| order_items | quantity_positive | quantity > 0 | error |
| order_items | price_non_negative | unit_price ≥ 0 | error |
| order_items | discount_range | discount_pct BETWEEN 0 AND 0.5 | warn |
| order_items | product_exists | product_id exists in silver.products | error (**retryable**) |

**Retryable quarantine:** rows that failed *only* `product_exists` are re-checked at the start of every
silver run. If the product has since arrived they are loaded and marked `_resolved_ts` in the quarantine
table. silver.products is therefore always processed **before** silver.order_items.

**Pipeline-level checks** (`nb_05_validate`, fail the run on error):

| Check | Severity |
|---|---|
| Primary keys unique in silver and gold | error |
| Exactly one `is_current` row per customer in dim_customer | error |
| No null surrogate keys in fact_sales | error |
| Row count: fact_sales = silver.order_items | error |
| Sum: fact booked_amount = silver line_amount (± 0.01) | error |
| No `product_sk = -1` in fact_sales (orphans are quarantined) | error |
| Unresolved orphan order lines older than 3 days | warn |
| Quarantine rate per entity in today's run < 5% | warn |
| Freshness: max order_date in fact ≥ run_date − 1 | error |

---

## 8. Logging & observability

**Python logging** (visible in notebook output and pipeline run details):
```
2026-11-03 06:00:12 | INFO | nb_03_silver | run=4f1c… | orders | merged rows=1532 quarantined=14
```

**Ops tables** (queryable from the SQL endpoint):

| Table | One row per | Key columns |
|---|---|---|
| ops.run_log | job execution | run_id, parent_run_id, job_name, run_date, status, start_ts, end_ts, duration_sec, metrics_json, error_message |
| ops.file_log | landing file loaded | file_path, entity, batch_date, row_count, run_id, loaded_ts |
| ops.watermark | (pipeline, source) | watermark_value, updated_ts |
| ops.dq_results | rule × entity × run | rule_name, severity, total_count, failed_count, passed |

`parent_run_id` = pipeline run id, so all steps of one daily run can be grouped.

---

## 9. Orchestration

`pl_retail_daily` (Fabric Data Pipeline), schedule **06:00 UTC daily**.

```
nb_01_generate_source → nb_02_bronze → nb_03_silver → nb_04_gold → nb_05_validate
                                          (on any failure) → notify (Outlook/Teams activity)
```

| Parameter | Default | Meaning |
|---|---|---|
| run_date | empty = today (UTC) | business date being processed |
| generate_source | true | false in a "real" environment (no simulator) |

Retries: 1 retry, 5 min interval per notebook activity. Timeout: 30 min per activity.

---

## 10. Environments & configuration

| | Dev | Prod |
|---|---|---|
| Workspace | ws_retail_dev | ws_retail_prod |
| Lakehouse | lh_retail | lh_retail |
| Git branch | dev | main |
| Generator history on first run | 30 days | 30 days |
| Schedule | manual | daily 06:00 UTC |

Config lives in `nb_config`. Environment is detected from the workspace name
(`ws_retail_<env>`). No secrets are needed (everything is inside Fabric).

---

## 11. Testing strategy

| Level | What | Where | When |
|---|---|---|---|
| Unit | pure functions in `nb_lib_*` with small hand-made DataFrames (dedupe, DQ, SCD2, fact calc) | `nb_test_unit` (Fabric); pytest in CI (Phase 8) | every change |
| Integration | full pipeline for 3 consecutive days in dev, plus a re-run (idempotency) | ws_retail_dev | before each PR |
| Data tests | pipeline-level checks (§7) | `nb_05_validate` | every run, incl. prod |

---

## 12. Development workflow & CI/CD

- Develop in `ws_retail_dev` → export notebooks → review → commit to `dev` → PR `dev → main` ([workflow](workflow.md)).
- CI (GitHub Actions, Phase 8): lint (ruff) + unit tests on every PR.
- CD (Phase 9): Fabric deployment pipeline `ws_retail_dev → ws_retail_prod`.

---

## 13. Decisions log

| # | Decision | Alternatives considered | Reason |
|---|---|---|---|
| D1 | Single lakehouse with schemas | One lakehouse per layer | Simpler for a mini project; schemas still separate layers |
| D2 | Bronze stores everything as string | Typed bronze | Bronze never fails on bad data; typing + DQ happen in silver |
| D3 | Watermark + file log for increments | Structured Streaming, Delta CDF | Explicit and easy to inspect/debug while learning |
| D4 | Hash-based surrogate keys | Identity / sequence | Deterministic, re-runnable, no state |
| D5 | Logic in library notebooks (`%run`) | Python wheel in Environment | Fast dev loop; migrate to wheel in Phase 8 |
| D6 | Manual Git sync | Fabric Git integration | GitHub sync blocked by tenant policy |
| D7 | Quarantine orphan order lines (retryable) | Load as Unknown product | Product-level reporting must be complete; late products self-heal |
| D8 | Cancelled/returned → net 0, rows kept | Physically delete rows; negative amounts | Keeps audit trail and return-rate KPI; sales totals exclude them |

## 14. Open questions

None. Resolved: orphan lines → D7, returns → D8.
