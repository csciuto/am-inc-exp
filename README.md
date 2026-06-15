# American Income Explorer

An interactive visualization of the US household income distribution, built from
IPUMS CPS ASEC microdata (survey years 2023–2025 / income years 2022–2024).

It lets you filter the survey down to specific slices — by work status, income
type, state, age band, education, marital status, roommate status, and more —
and see the resulting weighted income distribution as dots, stacked bars, or a
100% normalized view, with reliability indicators on small samples.

It is a static, framework-free site: a single `index.html` plus a handful of
precomputed data files served from GitHub Pages. There is no backend and no build
step — the only generated-at-commit file is `docs/version.json` (see
[Versioning](#versioning)), and the only heavy computation is a one-shot local
preprocessor.

## The data

**Source:** [IPUMS CPS](https://cps.ipums.org/) — the Current Population Survey
Annual Social and Economic Supplement (CPS ASEC), the US Census Bureau / Bureau of
Labor Statistics survey that is the official source for national income and poverty
statistics. This build pools survey years **2023–2025** (income years 2022–2024).
The unit of analysis is the **household** (one record per household, taken from the
householder's person record); incomes are total household income (`HHINCOME`).

**Quality and how to read it.** CPS ASEC is a high-quality probability sample, but
it is a *sample*, so every number here is an estimate, not a census count:

- **Weighted.** All statistics use the household sampling weight (`ASECWTH`), so
  they represent the US household population, not the raw respondents. The "~N M
  households" figure is the sum of weights.
- **Sample reliability.** Each slice carries a reliability tier based on how many
  survey records back it (high ≥500, moderate 200–499, low 50–199, suppressed <50).
  Small slices show a warning or hide point estimates rather than implying false
  precision. Where available, medians show a BRR (balanced repeated replication)
  standard error.
- **Topcoding.** Very high incomes are censored by the Census Bureau at a yearly
  maximum; those households are flagged (⊕) and sit at the censored value, so the
  extreme right tail is compressed.
- **Derived-field caveats.** "Secondary work" is a *proxy* inferred from a
  reported hours differential, not a direct job count, and undercounts some
  multiple-job holders. Cohabiting-partner classification is underidentified in
  older data. See `codebook.json` `notes` for the full text.

CPS ASEC is free to use; cite IPUMS per their
[terms](https://cps.ipums.org/cps/citation.shtml) if you publish results.

## Quick start

### View the app

```bash
# Serve the docs/ directory over HTTP (the app fetches data files, so file:// won't work)
python3 -m http.server -d docs 8000
# open http://localhost:8000
```

The page loads `codebook.json`, `stats_precomputed.json`, and `national.arrow`
(D3 and Apache Arrow come from a CDN, so the browser needs internet access).

### Using the explorer

- **Filter** with the left sidebar tabs — *Demo* (age, sex, marital status,
  roommates, education, race/ethnicity, kids), *Geo* (metro, individual states),
  *Work* (work status, income type, householder income share, housing tenure,
  secondary hours), and *Survey* (year). Clicking a value filters to it; clicking
  again clears it; clicking other values in the same group adds them. Active
  filters appear as removable chips above the chart; **Clear all** resets.
- **Color** by any dimension from the *Group* tab (income type by default). The
  legend reflects the current coloring; clicking a legend swatch also filters by
  that value.
- **Switch views** with the Dots / Bars / 100% toggle. Dots places up to 3,000
  weighted-subsampled households on a log income axis; Bars shows a weighted
  stacked histogram; 100% normalizes each bin to its own total. Hover any bar
  segment for the income range, category, household count, and share of that band.
- **Select a range** by clicking **Range** in the toolbar, then dragging across the
  chart. A panel shows median, mean, and share of households within that income
  band, with a by-group breakdown available.
- **Read the stats** in the sidebar: median, mean, and P25/P75 for the current
  slice, with a reliability badge. The **By group** section breaks median/mean
  income down by the active color dimension.
- **Hover** any dot for the household's full detail (income, age, work status,
  location, weight, year).
- **Deep-link** any view: filters, state selections, color mode, view mode, and
  range selections are all reflected in the URL hash so you can bookmark or share.

### Run the tests

```bash
pip install -r requirements-test.txt
playwright install chromium
python3 -m pytest --browser chromium
```

- `tests/test_preprocess.py` — 79 pure-function unit tests (no I/O, no network)
- `tests/test_frontend.py` — 88 Playwright browser tests (need internet for the CDN scripts)

## How it works

```
preprocess.py            One-shot preprocessor (run locally when new CPS data drops)
docs/
  index.html             The entire frontend — self-contained, no framework
  data/
    codebook.json        Labels + state metadata (always loaded)
    stats_precomputed.json   BRR-computed stats for ~5,500 filter cells (always loaded)
    national.arrow       All households, columnar (always loaded; ~6MB)
    states/XX.arrow      Per-state extracts — emitted but NOT used by the frontend*
tests/                   Unit + browser tests
```

\* The frontend loads only `national.arrow` and filters it in the browser by the
`state` column. The per-state Arrow files are a leftover of an earlier
lazy-loading design; they are still produced but never fetched.

**Stats path:** for any single-value filter combination the app builds a lookup
key and reads a precomputed cell (which carries BRR standard errors). For
multi-value or unprecomputed slices it falls back to live weighted computation in
the browser (no SE on that path, since replicate weights are not shipped). Small
samples are flagged or suppressed via a reliability tier rather than shown as
confident numbers.

For the statistical methods, weighted estimators, BRR variance estimation, and
derived-variable definitions, see [`METHODOLOGY.md`](METHODOLOGY.md). For
architecture details and code conventions see [`CLAUDE.md`](CLAUDE.md). For the
original design intent, see [`PRD.md`](PRD.md).

## Regenerating the data

You need your own IPUMS CPS ASEC extract — a fixed-width `.dat.gz` plus its `.xml`
DDI codebook. Build it at [cps.ipums.org](https://cps.ipums.org/): choose the ASEC
samples for the years you want (this build uses **ASEC 2023, 2024, 2025**), add the
variables below, and select **fixed-width** output. The extract is person-level;
the preprocessor reads the household roster (for cohabiting and roommate detection)
and then collapses to one record per household.

**IPUMS variables to request** (the `WANTED` set in `preprocess.py`):

| Group | Variables |
|---|---|
| Identifiers / record | `YEAR`, `SERIAL`, `PERNUM`, `RELATE` |
| Weights | `ASECWTH` (household weight), `REPWTP1`–`REPWTP160` (replicate weights) |
| Geography | `REGION`, `STATEFIP`, `METFIPS` |
| Demographics | `AGE`, `SEX`, `RACE`, `HISPAN`, `MARST`, `EDUC`, `NCHILD`, `YNGCH` |
| Labor force | `EMPSTAT`, `CLASSWKR`, `WKSWORK2`, `UHRSWORKLY`, `UHRSWORKT`, `UHRSWORK1` |
| Income | `HHINCOME`, `INCTOT`, `INCWAGE`, `INCBUS`, `INCSS`, `INCWELFR`, `INCDIVID`, `INCRENT` |
| Housing | `SPMMORT` |

Notes:
- A few of these (`YEAR`, `SERIAL`, `PERNUM`, `ASECWTH`, `RELATE`) are usually
  preselected by IPUMS — that's fine, the loader just reads whatever the DDI
  declares and ignores anything else.
- `REPWTP1`–`REPWTP160` are added in one step by selecting the **replicate
  weights** in the extract options. They power the BRR standard errors and are
  dropped before any browser file is written — they never ship to the frontend.
- Any of the above can be missing and the preprocessor will degrade gracefully
  (the derived field falls back to a default), but the income, weight, geography,
  and labor-force fields are needed for the tool to be meaningful.

```bash
python preprocess.py \
  --input cps_00005.dat.gz \
  --ddi   cps_00005.xml \
  --output-dir ./docs/data
```

This writes `national.arrow`, the per-state files, `stats_precomputed.json`, and
`codebook.json`. Computing BRR standard errors takes roughly 25 minutes.
Pass `--skip-stats` to regenerate only the Arrow and codebook files (fast, ~2 min),
which is useful when iterating on derived variables.

Preprocessor dependencies: `numpy`, `pandas`, `pyarrow`.

When adding a new survey year, verify that year's topcode value against the
[IPUMS topcode tables](https://cps.ipums.org/cps/topcodes_tables.shtml) and add it
to `TOPCODES` in `preprocess.py`.

## Deployment

GitHub Pages serves the `docs/` directory directly. Because the raw IPUMS extract
is far too large to regenerate the data in CI, the generated data files the
frontend depends on — `docs/data/national.arrow`, `stats_precomputed.json`, and
`codebook.json` — **must be committed** for the live site to load. The
`docs/data/states/` directory can remain untracked, since the frontend never
fetches it.

## Versioning

The footer shows a build stamp (commit + date) so you can tell which version is
deployed. It comes from `docs/version.json`, which is the one file generated at
commit time — written by **`scripts/stamp-version.sh`**. To have it update
automatically, enable the tracked pre-commit hook once per clone:

```bash
git config core.hooksPath scripts/hooks
```

You can also run `scripts/stamp-version.sh` by hand before committing. The hash it
records is the **parent** commit (the hook runs before the new commit exists), so
the stamp identifies the commit a build is based on — expect a one-step lag, which
is normal for commit-time stamping without a CI build.

## Known bugs

None currently known. See [GitHub Issues](https://github.com/csciuto/am-inc-exp/issues) for anything reported after this writing.

## License

MIT — see [`LICENCE`](LICENCE).
