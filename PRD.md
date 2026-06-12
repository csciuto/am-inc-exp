Project: American Income Explorer
Static site, GitHub Pages, no backend, partitioned Arrow files

Purpose
Visualize the income distribution of US households using CPS ASEC microdata. The tool lets users filter to specific population slices and see the resulting weighted distribution with honest uncertainty communication.
Built for data nerds. 2-second load time is acceptable. No mobile-first constraints.

Hosting
GitHub Pages, /docs directory. No build step, no bundler, no server. Preprocessor runs locally when new CPS data drops (annually). Output goes directly into /docs.

File Structure
/docs/
  index.html                    (frontend, self-contained, no framework)
  /data/
    codebook.json               (~5KB, always loaded)
    stats_precomputed.json      (~1.1MB, always loaded)
    national.arrow              (~3-4MB gzipped, always loaded)
    /states/
      01.arrow                  (Alabama, ~20-80KB gzipped)
      02.arrow                  (Alaska)
      ...
      56.arrow                  (Wyoming)
      (one file per STATEFIP code, 50 files)
Total repo size: national.arrow + 50 state files + precomputed stats. Estimated ~25MB uncompressed across all Arrow files, ~8-10MB gzipped. Well within GitHub Pages limits.

Load Sequence
Page open (parallel fetches):
  → codebook.json
  → stats_precomputed.json  
  → national.arrow
  Total: ~5MB, ~1-2s on average connection
  → parse Arrow into columnar typed arrays
  → apply default filters
  → render

User selects a state:
  → fetch /data/states/25.arrow  (MA = STATEFIP 25)
  → ~30-80KB, <200ms
  → merge with national typed arrays or swap, rerender
  → live stats computed from state file for any multi-dim filter

User deselects state:
  → drop state data, revert to national.arrow

Data Source
IPUMS CPS ASEC, 3 years pooled (2021, 2022, 2023). User downloads their own IPUMS extract and runs the preprocessor. The preprocessor is the only computation — it runs once, outputs to /docs/data/, done.

Variables to Extract from IPUMS
Identifiers and weights:
  YEAR, SERIAL, HWTFINL
  REPWTP1-REPWTP160     (preprocessor only, never in browser)

Geography:
  STATEFIP, REGION, METAREA

Demographics:
  AGE, SEX, MARST, RACE, HISPAN, EDUC, NCHILD, YNGCH

Labor force:
  EMPSTAT, CLASSWKR, WKSWORK2, UHRSWORKLY, MULTJOB, NUMJOBS

Income components:
  HHINCOME, INCWAGE, INCBUS, INCSS, INCDIVID, INCRENT, INCTOT
Note: extract must include both household and person records. Cohabiting partner detection requires a person-level roster join before collapsing to household level. See derived variables below.

Derived Variables (Preprocessor)
age_bucket (uint8, 8 values):
0: 18-24
1: 25-29
2: 30-34
3: 35-39
4: 40-44
5: 45-54
6: 55-64
7: 65+
sex (uint8): 1=Male, 2=Female
marst (uint8, 6 values):
0: Married, spouse present (MARST=1)
1: Cohabiting — householder with unmarried partner on household roster
   Requires person-level join: look for RELATE=1114 in household members
   Flag in codebook: cohabiting underidentified pre-2007
2: Separated or divorced (MARST=2,3,4)
3: Widowed (MARST=5)
4: Never married (MARST=6), not cohabiting
educ (uint8, 5 values):
0: Less than HS    (EDUC < 60)
1: HS diploma      (EDUC 60-72)
2: Some college    (EDUC 73-109)
3: Bachelor's      (EDUC 110)
4: Graduate        (EDUC 111+)
region (uint8): 1=Northeast, 2=Midwest, 3=South, 4=West
metro (uint8): 0=Non-metro, 1=Metro (from METAREA, 0=non-metro)
kids (uint8 boolean): NCHILD > 0
youngest_child (uint8):
0: No children
1: Under 5
2: 5-12
3: 13-17
Derived from YNGCH.
work_status (uint8, 7 values, hierarchy applied in order):
0: Full-time wage
   EMPSTAT at work (10-12)
   AND WKSWORK2 >= 5 (50-52 weeks)
   AND UHRSWORKLY >= 35
   AND CLASSWKR in {21,22,23,24,25,26,27,28} (private or govt, not self-employed)

1: Full-time self-employed
   Same conditions but CLASSWKR in {13,14}

2: Full-time part-year
   EMPSTAT at work
   AND UHRSWORKLY >= 35
   AND WKSWORK2 < 5

3: Part-time
   EMPSTAT at work
   AND UHRSWORKLY < 35

4: Unemployed
   EMPSTAT in {20,21,22}

5: Retired
   EMPSTAT not in labor force (30-36)
   AND INCSS > 0
   AND AGE >= 55

6: Not working
   Everything else not in labor force
hours_category (uint8):
0: Full-time (UHRSWORKLY >= 35)
1: Part-time (UHRSWORKLY 15-34)
2: Marginal  (UHRSWORKLY < 15)
3: N/A       (not employed)
weeks_worked (uint8, midpoint of WKSWORK2 band):
WKSWORK2=1 → 7
WKSWORK2=2 → 20
WKSWORK2=3 → 33
WKSWORK2=4 → 43
WKSWORK2=5 → 48
WKSWORK2=6 → 51
0 → 0 (did not work)
job_count (uint8):
1    if MULTJOB = 2 (no)
2-4  from NUMJOBS, cap at 4
multi_job (uint8 boolean): job_count > 1
income_type (uint8):
wage_income  = INCWAGE + INCBUS
passive      = INCDIVID + INCRENT
transfers    = INCSS + other transfers
wage_share   = wage_income / HHINCOME (where HHINCOME > 0)

0: Primarily wages        (wage_share >= 0.75)
1: Mixed                  (wage_share 0.25-0.74)
2: Primarily passive/transfer (wage_share < 0.25)
3: Zero or negative income
topcoded (uint8 boolean):
Flag if HHINCOME >= topcode threshold for that year.
Topcode values by year stored in codebook.json.
Verify against IPUMS documentation — topcodes change annually.
race_ethnicity (uint8, collapsed):
0: White non-Hispanic
1: Black non-Hispanic
2: Hispanic (any race)
3: Asian non-Hispanic
4: Other/multiracial
Derived from RACE + HISPAN combination.

Arrow Schema for Browser Files
All columns uint8 except:
id:         uint32
inc:        uint32   (HHINCOME)
wage_inc:   uint32   (INCWAGE)
weight:     float32  (HWTFINL)
state:      uint8    (STATEFIP — numeric code, mapped via codebook)
year:       uint8    (0=2021, 1=2022, 2=2023)
All other derived variables: uint8 as coded above.
Estimated sizes:

~20 columns × 240k rows × avg 2 bytes = ~9.6MB uncompressed national file
Gzipped: ~3-4MB
Per state: proportional to population. CA ~500KB gzipped, WY ~15KB gzipped


Precomputed Stats: stats_precomputed.json
BRR standard errors are expensive — compute them in the preprocessor, not the browser.
What to precompute:
National × single dimension (marginals):
  age_bucket:     8 slices
  sex:            2
  marst:          6
  educ:           5
  region:         4
  work_status:    7
  income_type:    4
  kids:           2
  race_ethnicity: 5
  Total: ~43 slices

National × two dimensions (all combinations):
  43² / 2 ≈ ~900 combinations
  Filter out any with n < 50 at compute time

Per-state × age_bucket:
  50 × 8 = 400 combinations

Per-state × work_status:
  50 × 7 = 350 combinations

Per-state × income_type:
  50 × 4 = 200 combinations

Per-region × two dimensions:
  4 × ~900 = ~3,600 combinations

Total meaningful cells: ~5,500
Stats stored per cell:
json{
  "n": 28420,
  "n_rep": 12400000,
  "med": 78000,
  "mean": 94200,
  "p25": 48000,
  "p75": 128000,
  "iqr": 80000,
  "sd": 62000,
  "se_med": 1200,
  "ci_lo": 93400,
  "ci_hi": 95000,
  "multi_job_pct": 0.14,
  "wage_pct": 0.68,
  "rel": 2
}
rel is reliability tier: 3=high (n≥500), 2=moderate (200-499), 1=low (50-199), 0=insufficient (<50).
Short keys because this gets repeated ~5,500 times.
Key format:
"national"
"national|age=2"
"national|age=2|marst=0"
"region=1|age=2|marst=0"
"state=25|age=2"
"state=25|work=0"
Estimated file size:
5,500 cells × ~200 bytes = ~1.1MB. Load once, keep in memory.

Stats Function — Browser
javascriptfunction getStats(filters, rows, weights) {
  // Build lookup key from filters
  const key = buildKey(filters);
  
  // Check precomputed lookup first
  if (precomputed[key]) {
    return { ...precomputed[key], source: 'precomputed' };
  }
  
  // Fall back to live computation
  const matched = filterRows(rows, filters);
  
  if (matched.length < 50) {
    return { n: matched.length, rel: 0, source: 'live' };
  }
  
  return {
    n: matched.length,
    n_rep: sumWeights(matched, weights),
    med: weightedMedian(matched, weights),
    mean: weightedMean(matched, weights),
    p25: weightedPercentile(matched, weights, 0.25),
    p75: weightedPercentile(matched, weights, 0.75),
    iqr: p75 - p25,
    sd: weightedStdDev(matched, weights),
    multi_job_pct: weightedShare(matched, 'multi_job', 1),
    wage_pct: weightedShare(matched, 'income_type', 0),
    rel: matched.length >= 500 ? 3 : matched.length >= 200 ? 2 : 1,
    source: 'live'
  };
  // Note: no se_med on live path — no replicate weights in browser
}

Stats Display by Reliability Tier
Tier 3 — High (n≥500, precomputed):
Median:  $78,000  ±$1,200 SE
Mean:    $94,200  ($93,400 – $95,000 95% CI)
P25/P75: $48,000 / $128,000
IQR:     $80,000
Multi-job holders: 14%
Primarily wage income: 68%
Based on 28,420 survey records · ~12.4M households
Tier 2 — Moderate (200-499, precomputed or live):
Same as above but add: ⚠ Moderate sample size — interpret with care
SE shown if precomputed, omitted if live.
Tier 1 — Low (50-199, always live):
Unweighted median: $71,000
⚠ Small sample (n=143) — statistics unreliable
  Showing dots only. Use broader filters for reliable estimates.
Show dots. Show unweighted median. Suppress everything else.
Tier 0 — Insufficient (<50):
⚠ Too few records (n=31) for statistics
  Showing raw survey records.
Dots only. No stats at all.

Dot Display
Geometry:

X axis: HHINCOME, log scale. Log handles right skew and makes lower-income variation visible.
Y axis: beeswarm jitter within income band, or user-selectable second dimension
One dot per matched record after weighted subsampling (max 3,000 dots from matched set)
Weighted subsampling: P(selection) proportional to weight, so dot density is visually honest

Dot sizing:

Radius proportional to weight
Normalize: median weight in current filtered set = 4px radius
Floor: 2px, ceiling: 8px
Recompute normalization on each filter change

Dot color — user selectable, toggle buttons:

Default: income_type (green=wages, yellow=mixed, gray=passive/transfer)
Multi-job: 1=gray, 2=yellow, 3=orange, 4+=red
Education: 5-color scale
Marital status: 5-color scale
Work status: 7-color scale
Race/ethnicity: 5-color scale

Topcoded dots: hollow circle, ⊕ symbol, always labeled in legend
Hover tooltip:
Household income: $94,000  ⚑ topcoded
Wage income: $87,000
Age: 30-34 · Married · Bachelor's
Massachusetts · Metro
Full-time wage · 1 job
Kids present (youngest under 5)
Weight: 1,847 households represented
Year: 2022

Multi-Job Feature
First-class feature, not a filter.
Toggle button in toolbar: "Color by job count"
When on: dots colored 1=gray, 2=yellow, 3=orange, 4+=red regardless of other color selection.
Stats panel always shows multi_job_pct for current slice.
Tooltip on multi_job_pct stat (always visible):

"CPS captures multiple jobs held during the survey reference week only. Workers who held multiple jobs at different points during the year are not counted here. True multiple job holding is likely higher, particularly at lower income levels."


Filter UI
Toolbar above chart. Each dimension is a multi-select toggle group — click to add/remove. Active filters shown as removable chips. "Clear all" button.
Dimensions available as filters:

Age bucket
Sex
Marital status
Education
Region (always available)
State (triggers state Arrow file fetch if not already loaded)
Work status
Job count (1 / 2 / 3 / 4+)
Kids present
Income type
Race/ethnicity
Metro / non-metro

State and region are mutually exclusive — selecting a state deactivates region filter and vice versa.

Preprocessor CLI
bashpython preprocess.py \
  --input cps_2021.csv cps_2022.csv cps_2023.csv \
  --output-dir ./docs/data \
  --years 2021 2022 2023

# Steps:
# 1. Load and merge all input files
# 2. Person-level join for cohabiting partner detection
# 3. Collapse to household level
# 4. Derive all variables
# 5. Write national.arrow
# 6. Split by STATEFIP, write 50 state Arrow files
# 7. Compute BRR stats for ~5,500 precomputed cells (slow — ~20-30 min)
# 8. Write stats_precomputed.json
# 9. Write codebook.json

# Progress output:
# [1/9] Loading CPS files... 241,847 households
# [2/9] Cohabiting partner detection... 18,243 flagged
# [3/9] Deriving variables...
# [4/9] Writing national.arrow... 9.2MB
# [5/9] Writing state files... 50 files, 8.1MB total
# [6/9] Computing precomputed stats...
#        National 1D: 43/43
#        National 2D: 847/900 (53 suppressed, n<50)
#        State slices: 950/950
#        Region 2D: 3,412/3,600 (188 suppressed)
#        Total: 5,252 cells written
# [7/9] Writing stats_precomputed.json... 1.1MB
# [8/9] Writing codebook.json...
# [9/9] Done. Output in ./docs/data
#
# Exclusions:
#   Missing HHINCOME:     1,243 records dropped
#   Negative HHINCOME:      264 records kept, flagged
#   Topcoded HHINCOME:      892 records kept, flagged
#
# Estimated preprocessing time for BRR: ~25 minutes

Dependencies
Preprocessor: pandas, numpy, pyarrow, weightedstats
Frontend: D3 v7, Apache Arrow JS (apache-arrow from CDN). No framework, no bundler.

Methodology Footer (always visible)

Data: IPUMS CPS ASEC 2021–2023, pooled. Each dot represents one survey household weighted by sampling weight. Statistics are weighted. Default view excludes households where less than 75% of income is from wages or self-employment, and excludes retired households. Multiple job data reflects the Census Bureau reference week only — annual multiple job holding is undercounted. Topcoded incomes (⊕) are censored at the year's maximum reported value. Small samples produce unreliable estimates — see reliability indicator. [Methodology] [GitHub]


What This Is Not
No map. No time series (though the year field enables it later). No cost-of-living adjustment. No occupation breakdown (not in scope for v1 — would require person-level aggregation). No comparison mode. These are v2 features.
