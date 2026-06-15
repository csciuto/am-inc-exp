# Methodology

This document covers the statistical methods and derivation rules behind
`preprocess.py`. For architecture and code conventions see [`CLAUDE.md`](CLAUDE.md);
for how to run the preprocessor see [`README.md`](README.md).

## Survey design and weighting

The source data is IPUMS CPS ASEC — the Annual Social and Economic Supplement
to the Current Population Survey, conducted by the Census Bureau and BLS. CPS
ASEC is a stratified, multi-stage probability sample. It is the official source
for US income and poverty statistics.

The unit of analysis here is the **household**. CPS ASEC is a person-level
file; the preprocessor collapses it to one record per household (using the
householder's person record as the anchor) after reading the full roster for
household-composition derivations.

**Sampling weight (`ASECWTH`)** is the household's probability-of-selection
weight. It is the number of US households that survey record represents.
All statistics — medians, means, percentiles, shares — are computed using
this weight. Summing weights gives an estimate of the total US household
count (~130M).

## Weighted statistics

### Median and percentiles

The weighted percentile at quantile *q* is computed via the empirical CDF:

1. Sort records by income.
2. Compute cumulative weight.
3. Find the first index where cumulative weight ≥ *q* × total weight.
4. Return that record's income value.

This is the "lower" or "type 1" definition — no interpolation between adjacent
values. It matches what most official statistics use for survey microdata.

```python
def w_percentile(values, weights, q):
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    idx = np.searchsorted(cum, q * cum[-1])
    return v[min(idx, len(v) - 1)]
```

The median is `w_percentile(values, weights, 0.5)`.

### Mean and standard deviation

Weighted mean: `Σ(wᵢ × xᵢ) / Σwᵢ` — standard frequency-weight formula.

Weighted standard deviation: `sqrt(Σ(wᵢ × (xᵢ − μ)²) / Σwᵢ)` where μ is the
weighted mean. Note this is the population SD, not the sample SD — the
denominator is total weight, not total weight minus one.

### Weighted share

`Σwᵢ [where condition] / Σwᵢ` — the weighted fraction of households meeting
some condition. Used for `wage_pct` and `multi_job_pct` in the stats cells.

## BRR standard errors

Balanced Repeated Replication (BRR) is the Census Bureau's recommended method
for variance estimation in CPS ASEC. It accounts for the complex survey design
(stratification, clustering) in a way that simple formulas cannot.

CPS ASEC provides **160 replicate weights** (`REPWTP1`–`REPWTP160`). Each
replicate weight set is a perturbation of the full-sample weight: roughly half
the primary sampling units in each stratum are up-weighted and the other half
down-weighted. Applying the estimator to each replicate gives 160 perturbed
estimates, whose spread quantifies sampling variance.

This implementation uses **Fay's modified BRR** with perturbation factor k = 0.5:

```
Var(θ̂) = Σᵣ (θ̂ᵣ − θ̂)² / [R × (1 − k)²]
```

where θ̂ is the full-sample estimate, θ̂ᵣ is the estimate using the r-th
replicate weight, R = 160, and k = 0.5. The denominator simplifies to
160 × 0.25 = 40.

```python
def brr_se_median(values, weights, rep_weights):
    k = 0.5
    theta = w_median(values, weights)
    sq = [(w_median(values, rw) - theta) ** 2 for rw in rep_weights]
    variance = sum(sq) / (len(sq) * (1 - k) ** 2)
    return np.sqrt(variance)
```

BRR SEs are computed only for precomputed cells (where the 160 replicate weight
columns are available). The live browser path has no BRR — replicate weights are
not shipped to the frontend. The 95% CI on the mean is ±1.96 × SE(mean).

BRR takes ~25 minutes because it runs the weighted estimator 160× for each of
~5,500 precomputed cells. Pass `--skip-stats` to the preprocessor to skip this
and regenerate only the Arrow and codebook files (~2 min).

## Derived variables

### work_status

A strict hierarchy: each household is assigned the highest-ranked status that
applies, in order:

| Value | Label | Conditions |
|---|---|---|
| 0 | Full-time wage | EMPSTAT in {10–12}, WKSWORK2 ≥ 5 (50–52 wks), UHRSWORKLY ≥ 35, CLASSWKR in wage/salary codes |
| 1 | Full-time self-emp | Same, but CLASSWKR in self-employed codes {13, 14} |
| 2 | FT part-year | At work, UHRSWORKLY ≥ 35, WKSWORK2 < 5 |
| 3 | Part-time | At work, UHRSWORKLY < 35 |
| 4 | Unemployed | EMPSTAT in {20–22} |
| 5 | Retired | Not in labor force, INCSS > 0, AGE ≥ 55 |
| 6 | Not working | Everything else |

This is the householder's status. It does not reflect other household members.

### income_type

Classifies the household by its primary income source. `wage_share` is the
sum of INCWAGE + INCBUS **across all household members** divided by HHINCOME:

| Value | Label | Condition |
|---|---|---|
| 0 | Primarily wages | wage_share ≥ 0.75 |
| 1 | Mixed | 0.25 ≤ wage_share < 0.75 |
| 2 | Primarily passive/transfer | 0 < wage_share < 0.25 |
| 3 | Zero or negative income | HHINCOME ≤ 0 |

The sum over all members matters: a household where the householder earns
nothing but a spouse earns wages should be classified as wages, not passive.
Earlier versions summed only the householder's income — that misclassified a
large share of multi-earner households.

### multi_job_proxy

CPS fields `MULTJOB` and `NUMJOBS` ask about the survey reference week only
and are unreliable. Instead, this is derived from hours:

```
secondary_hours = UHRSWORKT − UHRSWORK1
```

where UHRSWORKT is total hours across all jobs and UHRSWORK1 is hours on the
primary job. NIU codes (≥ 997 in either field) are excluded.

| Value | Label | Hours differential |
|---|---|---|
| 0 | No secondary work | ≤ 0 |
| 1 | Light (1–14 hrs) | 1–14 |
| 2 | Moderate (15–29 hrs) | 15–29 |
| 3 | Heavy (30+ hrs) | ≥ 30 |

This undercounts true multiple-job holding — it captures only people whose
hours differential is non-zero during the reference week. Annual multiple
job holding is higher.

### marst (marital/cohabiting status)

IPUMS `MARST` gives official marital status. Cohabiting partners are detected
from the household roster before collapse:

- Look for any household member with `RELATE` in {1114, 1116, 1117}
  (unmarried partner, same-sex partner variants)
- If found, override the householder's marital status to 1 (Cohabiting)

| Value | Label |
|---|---|
| 0 | Married, spouse present |
| 1 | Cohabiting (partner on roster) |
| 2 | Separated or divorced |
| 3 | Widowed |
| 4 | Never married, not cohabiting |

Cohabiting detection requires `RELATE` codes introduced in the mid-2000s and
may undercount some arrangements in older surveys.

### has_roommate

Detected from the household roster: any non-householder with `RELATE` in
{1113 (roomer/boarder), 1115 (housemate/roommate)}.

Note that CPS coding is conservative: people in genuinely shared-cost
arrangements who don't identify clearly as a "housemate" may be coded
as `RELATE=1116` (other non-relative) and are not captured here. The true
roommate rate is likely higher than the ~2.3% this flag shows.

### hh_share (householder income share)

`HHINCOME` is household income — it pools all members' income. `hh_share`
measures how economically concentrated that income is:

```
share = INCTOT_householder / HHINCOME   (clamped to [0, 1])
```

| Value | Label | Range |
|---|---|---|
| 0 | ≥90% sole earner | share ≥ 0.90 |
| 1 | 50–90% primary | 0.50 ≤ share < 0.90 |
| 2 | 25–50% equal/secondary | 0.25 ≤ share < 0.50 |
| 3 | <25% minor contributor | share < 0.25 (HHINCOME > 0) |

Households with zero or negative HHINCOME get value 3 as a fallback.

This dimension exists because the same HHINCOME of $120k means something
different for a sole earner versus two partners each earning $60k. It is
most useful when combined with the income distribution chart — filtering to
`hh_share=0` shows households where one person's income effectively IS the
household income.

### race_ethnicity

Hispanic origin takes precedence over race (matching standard Census practice):

| Value | Label | Rule |
|---|---|---|
| 0 | White non-Hispanic | RACE=100, HISPAN=0 |
| 1 | Black non-Hispanic | RACE=200, HISPAN=0 |
| 2 | Hispanic (any race) | HISPAN > 0 |
| 3 | Asian non-Hispanic | RACE in {651, 652}, HISPAN=0 |
| 4 | Other/multiracial | Everything else |

### housing (tenure)

From `SPMMORT` (Supplemental Poverty Measure mortgage indicator):

| Value | Label | SPMMORT |
|---|---|---|
| 0 | Owns with mortgage | 1 |
| 1 | Owns free and clear | 2 |
| 2 | Rents | 3 |

`SPMMORT=0` (NIU) maps to a suppressed value and is excluded from this filter.

## Precomputed stats structure

The precomputed table (`stats_precomputed.json`) caches weighted stats and BRR
standard errors for ~5,500 commonly-filtered cells, covering:

- **National × 1D**: each of the 11 dimensions in `DIMS`, all values
- **National × 2D**: all pairs of dimensions (suppressed if n < 50)
- **State × 1D**: each state × each dimension in `DIMS`
- **Region × 2D**: each of the 4 regions × all dimension pairs

Key format: `"scope|dim=val|dim=val"` with dims alphabetically sorted.
Examples: `"national"`, `"national|age=2|marst=0"`, `"state=25|work=0"`.

The frontend builds a lookup key from active filters. On a hit it uses the
precomputed stats (with BRR SE if available). On a miss it falls back to live
weighted computation in the browser — same formulas, no SE, and no
replicate-weight correction for complex survey design.
