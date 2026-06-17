# American Income Explorer

Static site hosted on GitHub Pages (`/docs`). No build step, no bundler, no server. Everything runs in the browser or as a one-shot local preprocessor.

> **Deploy gotcha:** the frontend fetches `docs/data/{codebook.json,stats_precomputed.json,national.arrow}` at runtime. The raw IPUMS extract is too large for CI, so these generated files cannot be rebuilt on GitHub. They must be committed for the deployed Pages site to work. Keep `docs/data/national.arrow`, `stats_precomputed.json`, and `codebook.json` tracked; `docs/data/states/` may stay ignored since the frontend never loads it.

## What this is

Visualizes US household income distribution using IPUMS CPS ASEC microdata (survey years 2021–2025, income years 2020–2024). Users filter the data to specific population slices (work status, income source, geography, demographics) and view the resulting weighted distribution with sample-reliability indicators.

## File structure

```
preprocess.py          One-shot preprocessor — run locally when new CPS data drops
docs/
  index.html           Entire frontend (self-contained, no framework)
  data/
    codebook.json      ~7KB, always loaded, labels + state metadata
    stats_precomputed.json  ~1.7MB, BRR-computed stats for ~9,256 cells
    national.arrow     ~12.8MB, always loaded — holds ALL states
    states/XX.arrow    Per-state files emitted by the preprocessor but NOT used by the
                       frontend (it filters national.arrow in-browser by the `state` column)
tests/
  conftest.py          Static HTTP server fixture (serves docs/)
  test_preprocess.py   81 unit tests for preprocess.py (pure functions)
  test_frontend.py     106 Playwright browser tests
  version.json         Commit hash + date, auto-updated by pre-commit hook
```

## Running tests

```bash
# Install once
pip install -r requirements-test.txt
playwright install chromium

# Run
python3 -m pytest --browser chromium
```

Frontend tests require internet access (D3 and Apache Arrow loaded from CDN).

## Preprocessor

Takes an IPUMS CPS ASEC fixed-width `.dat.gz` + `.xml` DDI codebook:

```bash
python preprocess.py --input cps_00006.dat.gz --ddi cps_00006.xml --output-dir ./docs/data
```

Outputs: `national.arrow`, `docs/data/states/XX.arrow` (50 files), `stats_precomputed.json`, `codebook.json`. BRR standard errors take ~8 min. Replicate weights (`REPWTP1–160`) are consumed by the preprocessor and never written to Arrow files.

The preprocessor is the only computation. The browser does live weighted stats only when the precomputed lookup misses.

## IPUMS extract requirements

When pulling a new CPS ASEC extract at https://cps.ipums.org/, request all of the following variables. The preprocessor will silently skip any that are absent from the DDI, but missing income vars corrupt `passive_source` classification (see note below).

**Always required:**

| Variable | Description |
|---|---|
| YEAR | Survey year |
| SERIAL | Household serial number |
| PERNUM | Person number within household |
| ASECWTH | Household weight |
| REPWTP1–160 | BRR replicate weights (160 vars) |
| REGION | Census region |
| STATEFIP | State FIPS code |
| METFIPS | Metro area FIPS |
| HHINCOME | Total household income |
| RELATE | Relationship to householder (cohabiting/roommate detection) |
| AGE | Age |
| SEX | Sex |
| RACE | Race |
| HISPAN | Hispanic origin |
| MARST | Marital status |
| NCHILD | Number of children |
| YNGCH | Age of youngest child |
| EDUC | Educational attainment |
| EMPSTAT | Employment status |
| CLASSWKR | Class of worker |
| WKSWORK2 | Weeks worked last year (bracketed) |
| UHRSWORKLY | Usual hours worked per week last year |
| UHRSWORKT | Hours worked all jobs, composite |
| UHRSWORK1 | Hours worked at main job |
| INCTOT | Total personal income |
| INCWAGE | Wage and salary income |
| INCBUS | Business/self-employment income |
| INCSS | Social Security and disability income |
| INCWELFR | Welfare/cash assistance income |
| INCDIVID | Dividend income |
| INCRENT | Rental income |
| INCRETIR | Retirement/pension income (**critical** — see note) |
| INCINT | Interest income (**important** — see note) |
| SPMMORT | Mortgage payment (used for housing cost variable) |

**Note on INCRETIR and INCINT:** Without INCRETIR, all households with large passive incomes (pension/retirement) are misclassified as "SS/disability" in the `passive_source` dimension, because SS wins `idxmax` among the tracked sources even with a small SS amount. INCINT (interest income) belongs in the capital income bucket alongside dividends and rent. Both were absent from extract `cps_00005` — **the current data files are affected**. Pull a new extract (`cps_00006`) with both variables before next release.

## Data schema

Arrow columns: `id` (uint32), `inc` (uint32, HHINCOME), `wage_inc` (uint32), `weight` (float32, ASECWTH), `state` (uint8, STATEFIP), `year` (uint8, 0=survey year 2021/income year 2020, 1=2022/2021, 2=2023/2022, 3=2024/2023, 4=2025/2024), then uint8 derived vars: `age_bucket`, `sex`, `marst`, `educ`, `region`, `metro`, `kids`, `youngest_child`, `work_status`, `hours_category`, `weeks_worked`, `multi_job_proxy`, `earner_count`, `breadwinner`, `passive_pct`, `passive_source`, `topcoded`, `race_ethnicity`, `has_roommate`, `hh_size`, `n_children`. Plus `passive_inc` (uint32).

Key derivations:
- `work_status`: hierarchy — FT wage (0) > FT self-emp (1) > FT part-year (2) > part-time (3) > unemployed (4) > retired (5) > not working (6)
- `multi_job_proxy`: derived from `UHRSWORKT − UHRSWORK1` (hours differential), NOT from `MULTJOB`/`NUMJOBS` — those are unreliable. NIU codes (≥997) excluded.
- `earner_count`: count of household members with INCWAGE+INCBUS > 0, clipped to 3 (meaning "3+")
- `breadwinner`: householder's share of household wage income — 0=sole (≥90%), 1=primary (60–89%), 2=co-earner (40–59%), 3=secondary (<40%), 4=no earner
- `passive_pct`: household wage share of total income — 0=≥75% wages, 1=50–74%, 2=25–49%, 3=<25% wages, 4=entirely passive/no wage income
- `passive_source`: dominant non-wage income source (winner-take-all by dollar, only set when wage_share < 0.75 and tracked passive income > 0) — 0=SS/disability (INCSS), 1=retirement/pension (INCRETIR), 2=capital (INCDIVID+INCRENT+INCINT), 3=welfare (INCWELFR), 4=N/A (wage dominant or no tracked passive)
- `passive_inc`: total passive income = max(0, HHINCOME − wage_inc), stored as uint32
- `marst`: cohabiting (1) detected via RELATE=1114/1116/1117 in household roster, overrides married (0)
- `has_roommate`: 1 if any household member has RELATE=1113 (roomer/boarder) or 1115 (housemate/roommate)
- `wage_inc`: sum of INCWAGE+INCBUS across all household members, after zeroing NIU sentinel (99,999,999)

## Precomputed stats keys

Format: `"scope|dim=val|dim=val"` (dims alphabetically sorted)
- `"national"` — no filters
- `"national|age=2|marst=0"` — national + two filters
- `"state=25|work=0"` — MA, full-time wage workers
- `"region=1|age=2"` — Northeast, age 30–34

Stats object: `{n, n_rep, med, mean, p25, p75, iqr, sd, se_med?, ci_lo?, ci_hi?, multi_job_pct, wage_pct, rel}`. `rel` tiers: 3=high (n≥500), 2=moderate (200–499), 1=low (50–199), 0=insufficient (<50, suppressed).

Dim short names used in keys: `work` (work_status), `ppct` (passive_pct), `psrc` (passive_source), `earners` (earner_count), `bread` (breadwinner), `age`, `sex`, `marst`, `educ`, `region`, `metro`, `kids`, `race`.

## Frontend architecture

Single `index.html`. Global state: `filters` (dim → Set of active values), `colorMode`, `nationalTable` (columnar typed arrays), `visibleRows`, `precomputed`, `codebook`.

No default filters on load — all households shown. Users filter via the sidebar tabs.

Stats lookup: builds key from active scope + single-value filters → hits `precomputed`, falls back to live weighted computation. Live path has no BRR SE (replicate weights not in browser).

Dot chart: log-scale x-axis, beeswarm y-jitter. Dot radius proportional to `weight` (median weight = 4px, floor 2px, ceiling 8px). Topcoded dots rendered as hollow circles with ⊕ overlay.

Dot sampling controls (toolbar, next to Dots/Bars/100%):
- **1K / 3K / 5K** — sets `maxDots` (default 3000), the upper limit of dots drawn. On mobile, capped further to `min(maxDots, 800)`.
- **Static** (default) — samples randomly from *all* rows, dims non-matching households via opacity. The same dot set persists as you change filters and color mode within a scope; new state/national selection re-samples.
- **All** — samples only from `visibleRows` (households matching current filters), re-samples on every filter change. Useful when filtered to a state or narrow slice where you want to see the actual population without dimmed background dots.

A "N of M households" counter appears in the toolbar when fewer than M are shown.

By group breakdown: computed from full `visibleRows` (not the dot subsample), weighted stats per group. Shows actual survey record count, not dot count.

Color modes: `passive_pct` (default), `work_status`, `age_bucket`, `sex`, `educ`, `marst`, `race_ethnicity`, `housing`, `kids`, `metro`, `multi_job_proxy`, `state`, `year`, `earner_count`, `breadwinner`, `passive_source`. Palettes in `PALETTES` object. Note: `sex` palette is sparse (index 0 = null, values 1=Male/2=Female) because CPS sex codes are 1/2; `renderLegend` skips null entries.

Sidebar tabs: Group (color mode picker), Demo (age/sex/marital/has_roommate/educ/race/kids), Geo (metro/states), Work (work_status/earner_count/breadwinner/passive_pct/passive_source/housing/multi_job_proxy), Survey (survey year filter).

## Cross-year methodology notes

The CPS ASEC was redesigned beginning with the 2023 survey (income year 2022). The main implications for this project:

- **RELATE codes**: Pre-2023, cohabiting partners used RELATE=1114 ("unmarried partner"). From 2023 onward, 1116 (opposite-sex) and 1117 (same-sex) replaced it. RELATE=1113 (roomer/boarder) was replaced by 1115 (housemate/roommate). The preprocessor handles all codes, so `marst` and `has_roommate` are correctly derived across all years.
- **Income variable availability**: INCRETIR and INCINT were present in all years including 2021–2022, so the new extract should fully populate `passive_source` across the entire 2020–2024 range.
- **COVID-era income anomalies**: Income years 2020–2021 (ASEC 2021–2022) include stimulus payments, enhanced unemployment benefits, and other pandemic-era transfers. These flow primarily into HHINCOME but are not separately tracked — they inflate `passive_inc` for some households in those years without a matching `passive_source` classification.
- **Topcode consistency**: HHINCOME topcode is $2,099,997 for all five years.

## Topcode values

```python
TOPCODES = {2023: 2099997, 2024: 2099997, 2025: 2099997}
```

Verify at https://cps.ipums.org/cps/topcodes_tables.shtml when adding new years.

## Adding a new color mode

1. Add palette to `PALETTES` in `index.html` (use `null` at unused indices for non-zero-based codes)
2. Add `<button class="cm-btn" data-mode="...">` in the color-mode div (`#tab-color`)
3. Add filter buttons container `<div class="filter-btns" id="f-...">` in the appropriate sidebar tab
4. Add an entry to `FILTER_DEFS` array and `DIM_LABELS` object
5. Add legend count test in `tests/test_frontend.py`
