# American Income Explorer

Static site hosted on GitHub Pages (`/docs`). No build step, no bundler, no server. Everything runs in the browser or as a one-shot local preprocessor.

> **Deploy gotcha:** the frontend fetches `docs/data/{codebook.json,stats_precomputed.json,national.arrow}` at runtime. The raw IPUMS extract is too large for CI, so these generated files cannot be rebuilt on GitHub. They must be committed for the deployed Pages site to work. Keep `docs/data/national.arrow`, `stats_precomputed.json`, and `codebook.json` tracked; `docs/data/states/` may stay ignored since the frontend never loads it.

## What this is

Visualizes US household income distribution using IPUMS CPS ASEC microdata (survey years 2023–2025, income years 2022–2024). Users filter the data to specific population slices (work status, income type, geography, demographics) and view the resulting weighted distribution with sample-reliability indicators.

## File structure

```
preprocess.py          One-shot preprocessor — run locally when new CPS data drops
docs/
  index.html           Entire frontend (self-contained, no framework)
  data/
    codebook.json      ~5KB, always loaded, labels + state metadata
    stats_precomputed.json  ~1.1MB, BRR-computed stats for ~5,500 cells
    national.arrow     ~2.9MB, always loaded — holds ALL states
    states/XX.arrow    Per-state files emitted by the preprocessor but NOT used by the
                       frontend (it filters national.arrow in-browser by the `state` column)
tests/
  conftest.py          Static HTTP server fixture (serves docs/)
  test_preprocess.py   76 unit tests for preprocess.py (pure functions)
  test_frontend.py     50 Playwright browser tests
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
python preprocess.py --input cps_00004.dat.gz --ddi cps_00004.xml --output-dir ./docs/data
```

Outputs: `national.arrow`, `docs/data/states/XX.arrow` (50 files), `stats_precomputed.json`, `codebook.json`. BRR standard errors take ~25 min. Replicate weights (`REPWTP1–160`) are consumed by the preprocessor and never written to Arrow files.

The preprocessor is the only computation. The browser does live weighted stats only when the precomputed lookup misses.

## Data schema

Arrow columns: `id` (uint32), `inc` (uint32, HHINCOME), `wage_inc` (uint32), `weight` (float32, ASECWTH), `state` (uint8, STATEFIP), `year` (uint8, 0=survey year 2023/income year 2022, 1=2024/2023, 2=2025/2024), then uint8 derived vars: `age_bucket`, `sex`, `marst`, `educ`, `region`, `metro`, `kids`, `youngest_child`, `work_status`, `hours_category`, `weeks_worked`, `multi_job_proxy`, `income_type`, `topcoded`, `race_ethnicity`, `has_roommate`.

Key derivations:
- `work_status`: hierarchy — FT wage (0) > FT self-emp (1) > FT part-year (2) > part-time (3) > unemployed (4) > retired (5) > not working (6)
- `multi_job_proxy`: derived from `UHRSWORKT − UHRSWORK1` (hours differential), NOT from `MULTJOB`/`NUMJOBS` — those are unreliable. NIU codes (≥997) excluded.
- `income_type`: wage_share = (INCWAGE+INCBUS)/HHINCOME; ≥0.75=wages, 0.25–0.74=mixed, <0.25=passive, zero/neg=3
- `marst`: cohabiting (1) detected via RELATE=1114/1116/1117 in household roster, overrides married (0)
- `has_roommate`: 1 if any household member has RELATE=1113 (roomer/boarder) or 1115 (housemate/roommate)

## Precomputed stats keys

Format: `"scope|dim=val|dim=val"` (dims alphabetically sorted)
- `"national"` — no filters
- `"national|age=2|marst=0"` — national + two filters
- `"state=25|work=0"` — MA, full-time wage workers
- `"region=1|age=2"` — Northeast, age 30–34

Stats object: `{n, n_rep, med, mean, p25, p75, iqr, sd, se_med?, ci_lo?, ci_hi?, multi_job_pct, wage_pct, rel}`. `rel` tiers: 3=high (n≥500), 2=moderate (200–499), 1=low (50–199), 0=insufficient (<50, suppressed).

## Frontend architecture

Single `index.html`. Global state: `filters` (dim → Set of active values), `colorMode`, `nationalTable` (columnar typed arrays), `visibleRows`, `precomputed`, `codebook`.

No default filters on load — all households shown. Users filter via the sidebar tabs.

Stats lookup: builds key from active scope + single-value filters → hits `precomputed`, falls back to live weighted computation. Live path has no BRR SE (replicate weights not in browser).

Dot chart: log-scale x-axis, beeswarm y-jitter, max 3000 dots via weighted subsampling. Dot radius proportional to `weight` (median weight = 4px, floor 2px, ceiling 8px). Topcoded dots rendered as hollow circles with ⊕ overlay.

By group breakdown: computed from full `visibleRows` (not the dot subsample), weighted stats per group. Shows actual survey record count, not dot count.

Color modes: income_type, work_status, age_bucket, sex, educ, marst, race_ethnicity, housing, kids, metro, multi_job_proxy, state, year. Palettes in `PALETTES` object. Note: `sex` palette is sparse (index 0 = null, values 1=Male/2=Female) because CPS sex codes are 1/2; `renderLegend` skips null entries.

Sidebar tabs: Group (color mode picker), Demo (age/sex/marital/has_roommate/educ/race/kids), Geo (metro/states), Work (work_status/income_type/hh_share/housing/secondary hours), Survey (survey year filter).

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
