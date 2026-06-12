# Snowflake Marketplace Submission Guide

This document covers the end-to-end process for listing Airbrx Cost Intelligence on the Snowflake Marketplace — from creating a provider profile to passing Snowflake's listing review.

---

## Overview of the process

```
1. Create provider profile (app.snowflake.com)  ←  ~14-day review
         ↓
2. Test the app locally (snow app run)
         ↓
3. Create a Marketplace listing
         ↓
4. Snowflake listing review                     ←  ~7-day review
         ↓
5. Publish — customers click Get
```

Both reviews run in parallel if you submit the provider profile and the listing at the same time. Total calendar time: approximately 14–21 days.

---

## Step 1 — Create a Snowflake Provider Profile

1. Go to [app.snowflake.com](https://app.snowflake.com) and log in with your Snowflake account
2. Navigate to **Provider Studio** (left sidebar)
3. Click **Become a Provider**
4. Fill in the provider profile:

| Field | Value |
|---|---|
| Company name | Airbrx, Inc. |
| Website | https://airbrx.ai |
| Support email | support@airbrx.ai |
| Privacy policy URL | https://airbrx.ai/privacy |
| Terms of service URL | https://airbrx.ai/terms |
| Description | Short paragraph about Airbrx |

5. Submit for review

> **Timeline:** Snowflake reviews provider profiles in approximately **14 business days**. Submit this as early as possible — it is the longest-lead item and blocks everything else.

---

## Step 2 — Prepare the application package

Before creating a listing, the app must be deployed to a production Snowflake account as an Application Package.

### 2a. Use a dedicated production Snowflake account

Do not use your development account for the Marketplace package. Use a dedicated provider account.

### 2b. Increment the version in `manifest.yml`

```yaml
version:
  name: v1
  label: "1.0.0"
  comment: "Initial release — cost-optimization report from Snowflake ACCOUNT_USAGE"
```

For subsequent releases, increment `label` (e.g., `"1.0.1"`, `"1.1.0"`) and update the `comment`.

### 2c. Deploy to production

```bash
.venv/bin/snow app run --connection prod
```

### 2d. Create a versioned release in the Application Package

```sql
-- Set the current version as the release directive for the listing
ALTER APPLICATION PACKAGE airbrx_cost_intelligence_pkg
  SET DEFAULT RELEASE DIRECTIVE
  VERSION = v1
  PATCH = 0;
```

---

## Step 3 — Create the Marketplace listing

In [app.snowflake.com](https://app.snowflake.com) → **Provider Studio** → **Create Listing**:

### Listing type

Select: **Application** (Snowflake Native App)

### Basic information

| Field | Value |
|---|---|
| Listing title | Airbrx Cost Intelligence |
| Short description (≤ 150 chars) | Identify redundant queries and measure realized savings from the Airbrx gateway — directly in your Snowflake account. |
| Category | Business Intelligence / Analytics |
| Sub-category | Cost Optimization |

### Long description

Use the following as a starting point (edit as needed):

```
Airbrx Cost Intelligence connects to your Snowflake ACCOUNT_USAGE system tables 
and automatically detects redundant query patterns, measures gateway coverage, 
and calculates realized savings per Airbrx rule.

**What it does:**
- Fingerprints every query in QUERY_HISTORY (no raw SQL stored or transmitted)
- Attributes cost directly from QUERY_ATTRIBUTION_HISTORY (no estimation needed)
- Tracks which queries the Airbrx gateway intercepted vs passed through
- Measures realized savings: baseline execution rate vs observed rate × avoided cost

**What you see in the UI:**
- Daily gateway coverage percentage
- Top redundant query patterns by execution count
- Warehouse cost trends (projected and realized savings)
- Per-rule attribution table with avoided counts and USD savings

**Privacy:**
No query text, warehouse IDs, or user names leave your account. Only aggregate 
rule-effectiveness statistics (rule_id, avoided count, realized_usd) are sent 
to api.airbrx.ai via HTTPS. A full network rule is included in the manifest.

**Requirements:**
- An active Airbrx subscription and API key
- EXECUTE TASK and IMPORTED PRIVILEGES ON SNOWFLAKE DB (requested at install)
```

### Application package

- Package name: `airbrx_cost_intelligence_pkg`
- Version: `v1`
- Patch: `0`

### Pricing

- **Free** (the app itself is free; Airbrx subscription is billed separately)
- Select: **Contact Provider** for the subscription model

### Regions

Select all Snowflake regions where you want the app available. For initial launch, select **AWS US East (N. Virginia)** and expand from there.

### Screenshots

Prepare 4–6 screenshots from the Streamlit UI:
1. Coverage screen — area chart showing daily coverage %
2. Redundant Spend — table of top repeated fingerprints
3. Projected vs Realized — bar chart by warehouse
4. Per-Rule Attribution — table with realized USD column
5. (Optional) The app install flow showing privilege grant screen

Screenshots must be 1280×800px or larger, PNG format.

---

## Step 4 — Security review requirements

Snowflake reviews all Native App listings before publishing. The review checklist includes:

### Network access declaration

The manifest declares a network rule for `api.airbrx.ai:443`. During review, Snowflake will verify:
- The rule is declared in `manifest.yml`
- The External Access Integration is created in `setup.sql`
- The procedure's `EXTERNAL_ACCESS_INTEGRATIONS` clause references it

Our `manifest.yml` currently does **not** declare the network rule as a privilege — it is created inside `setup.sql`. Before submission, verify that Snowflake's review process accepts this pattern, or add an explicit network rule reference to `manifest.yml`.

### Privilege justification

For each privilege in `manifest.yml`, reviewers will ask for justification:

| Privilege | Justification |
|---|---|
| `EXECUTE TASK` | Required to run the 2-hour scheduled analysis task that reads ACCOUNT_USAGE and populates state tables |
| `EXECUTE MANAGED TASK` | Required for serverless task execution (no warehouse required for the task itself) |
| `IMPORTED PRIVILEGES ON SNOWFLAKE DB` | Required to read `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` and `QUERY_ATTRIBUTION_HISTORY` — these views are gated behind this privilege |

### Data handling statement

Prepare a brief data handling statement for the review form:

> Airbrx Cost Intelligence does not store, transmit, or export any query text, user names, warehouse IDs, or row-level usage data. The only data that crosses the network boundary is aggregate rule-effectiveness statistics: rule_id (a string like "r42"), avoided (an integer), and realized_usd (a float). These are sent to api.airbrx.ai via HTTPS on port 443. The network destination is declared as a Host/Port network rule in the manifest.

---

## Step 5 — Submit for listing review

Once the application package is set up and the listing is complete:

1. Click **Submit for Review** in Provider Studio
2. Snowflake will send a confirmation email
3. Review typically takes **5–7 business days**
4. You may receive questions from the Snowflake review team — respond promptly

---

## Step 6 — After approval

Once the listing is approved:

1. **Publish** the listing from Provider Studio (it is not live until you click Publish)
2. Share the Marketplace URL with customers: `https://app.snowflake.com/marketplace/listing/<listing-id>`
3. Monitor install metrics in Provider Studio → **Listing Analytics**

---

## Releasing updates

When you push a new version:

1. Update `manifest.yml` — increment `label` (e.g., `"1.0.1"`)
2. Deploy: `snow app run --connection prod`
3. Add a new version to the package:
   ```sql
   ALTER APPLICATION PACKAGE airbrx_cost_intelligence_pkg
     ADD VERSION v1_1
     USING '@airbrx_cost_intelligence_pkg.app_schema.app_stage';
   ```
4. Update the release directive:
   ```sql
   ALTER APPLICATION PACKAGE airbrx_cost_intelligence_pkg
     SET DEFAULT RELEASE DIRECTIVE
     VERSION = v1_1 PATCH = 0;
   ```
5. Existing installs will be offered the upgrade notification in Snowsight

Minor patch updates (bug fixes) can be released without re-review. Major version bumps that add new privileges require a new listing review.

---

## Checklist before submission

- [ ] Provider profile submitted and approved
- [ ] App tested end-to-end with `snow app run` on a clean account
- [ ] `pytest tests/` — 32 tests pass
- [ ] `sync_log` shows `status = ok` after a manual `run_analysis('all')`
- [ ] All 4 Streamlit screens load without errors
- [ ] No API keys, passwords, or secrets committed to git (`git log --all --full-history -- '*.env' '*.yml'`)
- [ ] `manifest.yml` has accurate privilege descriptions
- [ ] Screenshots prepared (1280×800px+, PNG)
- [ ] Long description reviewed for accuracy
- [ ] Data handling statement prepared for review form
- [ ] Production account separate from development account
- [ ] Release directive set on the application package

---

## Contact

For Snowflake Marketplace questions: partner-marketplace@snowflake.com

For Airbrx listing questions: engineering@airbrx.ai
