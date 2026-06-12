"""
Unit tests for preprocess.py — pure functions and variable derivation.
No file I/O; synthetic DataFrames only.
"""
import sys
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import preprocess as pp


# ---------------------------------------------------------------------------
# Weighted stats helpers
# ---------------------------------------------------------------------------

def test_w_median_uniform():
    assert pp.w_median([1, 2, 3, 4, 5], [1, 1, 1, 1, 1]) == 3.0


def test_w_median_heavy_tail():
    # 99% weight on 10 pulls median there
    assert pp.w_median([1, 10], [1, 99]) == 10.0


def test_w_mean_equal_weights():
    assert pp.w_mean([0, 100], [1, 1]) == pytest.approx(50.0)


def test_w_mean_unequal_weights():
    assert pp.w_mean([0, 100], [3, 1]) == pytest.approx(25.0)


def test_w_percentile_p25_p75():
    vals = list(range(100))
    w = [1] * 100
    assert pp.w_percentile(vals, w, 0.25) == pytest.approx(25, abs=1)
    assert pp.w_percentile(vals, w, 0.75) == pytest.approx(75, abs=1)


def test_w_std_constant():
    assert pp.w_std([5, 5, 5], [1, 1, 1]) == pytest.approx(0.0)


def test_w_std_known():
    # population std of [0, 2] with equal weights = 1.0
    assert pp.w_std([0, 2], [1, 1]) == pytest.approx(1.0)


def test_w_share_all_true():
    assert pp.w_share([True, True, True], [1, 1, 1]) == pytest.approx(1.0)


def test_w_share_half():
    assert pp.w_share([True, False, True, False], [1, 1, 1, 1]) == pytest.approx(0.5)


def test_w_share_empty_weights():
    assert pp.w_share([], []) == 0.0


# ---------------------------------------------------------------------------
# build_key
# ---------------------------------------------------------------------------

def test_build_key_national_bare():
    assert pp.build_key("national", {}) == "national"


def test_build_key_with_filters():
    assert pp.build_key("national", {"age": 2, "marst": 0}) == "national|age=2|marst=0"


def test_build_key_filters_sorted():
    k1 = pp.build_key("national", {"marst": 0, "age": 2})
    k2 = pp.build_key("national", {"age": 2, "marst": 0})
    assert k1 == k2


def test_build_key_state_scope():
    assert pp.build_key("state=25", {"age": 2}) == "state=25|age=2"


def test_build_key_region_scope():
    assert pp.build_key("region=1", {}) == "region=1"


# ---------------------------------------------------------------------------
# compute_cell
# ---------------------------------------------------------------------------

def _frame(n, inc=50_000, w=1.0, mjp=0, itype=0):
    return pd.DataFrame({
        "inc":             np.full(n, inc, dtype=np.uint32),
        "weight":          np.full(n, w,   dtype=np.float32),
        "multi_job_proxy": np.full(n, mjp, dtype=np.uint8),
        "income_type":     np.full(n, itype, dtype=np.uint8),
    })


def test_compute_cell_suppressed_under_50():
    assert pp.compute_cell(_frame(49)) is None


def test_compute_cell_at_50_not_suppressed():
    assert pp.compute_cell(_frame(50)) is not None


def test_compute_cell_required_keys():
    cell = pp.compute_cell(_frame(100))
    for k in ("n", "n_rep", "med", "mean", "p25", "p75", "iqr", "sd",
              "multi_job_pct", "wage_pct", "rel"):
        assert k in cell, f"missing key: {k}"


def test_compute_cell_rel_tiers():
    assert pp.compute_cell(_frame(50))["rel"]  == 1
    assert pp.compute_cell(_frame(200))["rel"] == 2
    assert pp.compute_cell(_frame(500))["rel"] == 3


def test_compute_cell_median_correct():
    vals = np.arange(100, dtype=np.uint32) * 1000  # 0, 1000, ..., 99000
    df = _frame(100)
    df["inc"] = vals
    cell = pp.compute_cell(df)
    assert 48_000 <= cell["med"] <= 51_000


def test_compute_cell_multi_job_pct():
    df = _frame(100, mjp=1)   # all have secondary work
    assert pp.compute_cell(df)["multi_job_pct"] == pytest.approx(1.0)


def test_compute_cell_wage_pct():
    df = _frame(100, itype=0)  # all primarily-wage
    assert pp.compute_cell(df)["wage_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# brr_se_median
# ---------------------------------------------------------------------------

def test_brr_se_median_non_negative():
    rng = np.random.default_rng(0)
    vals = rng.integers(20_000, 200_000, 200).astype(float)
    w = np.ones(200)
    rep_ws = [w + rng.normal(0, 0.05, 200) for _ in range(10)]
    se = pp.brr_se_median(vals, w, rep_ws)
    assert se >= 0


def test_brr_se_mean_non_negative():
    rng = np.random.default_rng(1)
    vals = rng.integers(20_000, 200_000, 200).astype(float)
    w = np.ones(200)
    rep_ws = [w + rng.normal(0, 0.05, 200) for _ in range(10)]
    se = pp.brr_se_mean(vals, w, rep_ws)
    assert se >= 0


def _frame_with_reps(seed, n=400, n_reps=40):
    """compute_cell-ready frame with REPWTP columns.

    Incomes are heavy-right-tailed (lognormal) so the mean's sampling SE diverges
    noticeably from the median's — that gap is what lets the CI regression test
    below distinguish a mean-CI built from SE(mean) vs. SE(median).
    """
    rng = np.random.default_rng(seed)
    df = _frame(n)
    df["inc"] = np.clip(np.exp(rng.normal(10.8, 1.1, n)), 0, 5_000_000).astype(np.uint32)
    rep_cols = [f"REPWTP{i}" for i in range(1, n_reps + 1)]
    for c in rep_cols:
        df[c] = np.clip(np.ones(n) + rng.normal(0, 0.35, n), 0.05, None).astype(np.float32)
    return df, rep_cols


def test_compute_cell_emits_mean_ci_with_rep_weights():
    df, rep_cols = _frame_with_reps(seed=2)
    cell = pp.compute_cell(df, rep_cols)
    for k in ("se_med", "ci_lo", "ci_hi"):
        assert k in cell
    assert cell["ci_lo"] <= cell["mean"] <= cell["ci_hi"]


def test_compute_cell_mean_ci_uses_mean_se_not_median_se():
    # Regression: ci_lo/ci_hi are a CI around the MEAN, so the half-width must be
    # 1.96 * SE(mean), not 1.96 * SE(median) as an earlier version computed.
    df, rep_cols = _frame_with_reps(seed=3)
    cell = pp.compute_cell(df, rep_cols)
    rep_ws = [df[c].values for c in rep_cols]
    se_mean = pp.brr_se_mean(df["inc"].values, df["weight"].values, rep_ws)
    half_width = cell["ci_hi"] - cell["mean"]
    assert half_width == pytest.approx(1.96 * se_mean, rel=0.02)


# ---------------------------------------------------------------------------
# Synthetic household helper
# ---------------------------------------------------------------------------

def _make_hh(**overrides):
    defaults = dict(
        YEAR=2024, HHINCOME=80_000, INCWAGE=70_000,
        INCBUS=0, INCSS=0, INCDIVID=0, INCRENT=0, INCWELFR=0,
        ASECWTH=1500.0, STATEFIP=25, REGION=11, METFIPS=14460,
        AGE=35, SEX=1, RACE=100, HISPAN=0,
        MARST=1, EDUC=110, NCHILD=0, YNGCH=99,
        EMPSTAT=10, CLASSWKR=22, WKSWORK2=6,
        UHRSWORKLY=40, UHRSWORKT=40, UHRSWORK1=40,
        SPMMORT=1,
        _cohabiting=False,
    )
    defaults.update(overrides)
    return pd.DataFrame([defaults])


def _row(**overrides):
    out, _ = pp.derive_variables(_make_hh(**overrides))
    return out.iloc[0]


# ---------------------------------------------------------------------------
# age_bucket
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("age,bucket", [
    (22, 0), (27, 1), (32, 2), (37, 3), (42, 4), (50, 5), (60, 6), (70, 7),
])
def test_age_bucket(age, bucket):
    assert _row(AGE=age)["age_bucket"] == bucket


# ---------------------------------------------------------------------------
# work_status hierarchy
# ---------------------------------------------------------------------------

def test_work_status_ft_wage():
    assert _row(EMPSTAT=10, CLASSWKR=22, WKSWORK2=6, UHRSWORKLY=40)["work_status"] == 0


def test_work_status_ft_self_employed():
    assert _row(EMPSTAT=10, CLASSWKR=13, WKSWORK2=6, UHRSWORKLY=40)["work_status"] == 1


def test_work_status_ft_part_year():
    # full-time hours but < 5 weeks bands
    assert _row(EMPSTAT=10, CLASSWKR=22, WKSWORK2=3, UHRSWORKLY=40)["work_status"] == 2


def test_work_status_part_time():
    assert _row(EMPSTAT=10, UHRSWORKLY=20)["work_status"] == 3


def test_work_status_unemployed():
    assert _row(EMPSTAT=21)["work_status"] == 4


def test_work_status_retired():
    assert _row(EMPSTAT=33, INCSS=15_000, AGE=65)["work_status"] == 5


def test_work_status_not_working():
    # Not in labor force, no SS, too young → 6
    assert _row(EMPSTAT=33, INCSS=0, AGE=40)["work_status"] == 6


def test_work_status_wage_wins_over_self_emp():
    # wage_wkr check runs last (highest priority) — classwkr=22 is private wage
    assert _row(EMPSTAT=10, CLASSWKR=22, WKSWORK2=6, UHRSWORKLY=40)["work_status"] == 0


# ---------------------------------------------------------------------------
# income_type
# ---------------------------------------------------------------------------

def test_income_type_primarily_wages():
    assert _row(HHINCOME=80_000, INCWAGE=70_000)["income_type"] == 0


def test_income_type_mixed():
    assert _row(HHINCOME=80_000, INCWAGE=40_000)["income_type"] == 1


def test_income_type_passive():
    assert _row(HHINCOME=80_000, INCWAGE=10_000)["income_type"] == 2


def test_income_type_zero_income():
    assert _row(HHINCOME=0, INCWAGE=0)["income_type"] == 3


# ---------------------------------------------------------------------------
# marst
# ---------------------------------------------------------------------------

def test_marst_married_spouse_present():
    assert _row(MARST=1, _cohabiting=False)["marst"] == 0


def test_marst_cohabiting_overrides_married():
    assert _row(MARST=1, _cohabiting=True)["marst"] == 1


def test_marst_separated():
    assert _row(MARST=3, _cohabiting=False)["marst"] == 2


def test_marst_widowed():
    assert _row(MARST=5, _cohabiting=False)["marst"] == 3


def test_marst_never_married():
    assert _row(MARST=6, _cohabiting=False)["marst"] == 4


# ---------------------------------------------------------------------------
# educ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("educ,bucket", [
    (50,  0),   # < HS
    (70,  1),   # HS diploma (60–72)
    (90,  2),   # some college (73–109)
    (111, 3),   # bachelor's — IPUMS CPS code 111, not 110
    (123, 4),   # graduate (master's / professional / doctoral)
])
def test_educ_bucket(educ, bucket):
    assert _row(EDUC=educ)["educ"] == bucket


def test_educ_110_not_a_real_code():
    # EDUC=110 doesn't exist in current CPS data; it would fall in bucket 3
    # but EDUC=111 is the real Bachelor's code
    row = _row(EDUC=111)
    assert row["educ"] == 3  # Bachelor's


# ---------------------------------------------------------------------------
# race_ethnicity
# ---------------------------------------------------------------------------

def test_race_white_nonhisp():
    assert _row(RACE=100, HISPAN=0)["race_ethnicity"] == 0


def test_race_black_nonhisp():
    assert _row(RACE=200, HISPAN=0)["race_ethnicity"] == 1


def test_race_hispanic_any_race():
    assert _row(RACE=100, HISPAN=100)["race_ethnicity"] == 2


def test_race_asian_nonhisp():
    assert _row(RACE=651, HISPAN=0)["race_ethnicity"] == 3


def test_race_other():
    assert _row(RACE=300, HISPAN=0)["race_ethnicity"] == 4


# ---------------------------------------------------------------------------
# topcoded
# ---------------------------------------------------------------------------

def test_topcoded_at_threshold():
    assert _row(YEAR=2023, HHINCOME=pp.TOPCODES[2023])["topcoded"] == 1


def test_not_topcoded_below_threshold():
    assert _row(YEAR=2023, HHINCOME=100_000)["topcoded"] == 0


# ---------------------------------------------------------------------------
# multi_job_proxy
# ---------------------------------------------------------------------------

def test_mjp_no_secondary():
    assert _row(EMPSTAT=10, UHRSWORKT=40, UHRSWORK1=40)["multi_job_proxy"] == 0


def test_mjp_light_secondary():
    # 5 hrs differential → band 1–14 → code 1
    assert _row(EMPSTAT=10, UHRSWORKT=45, UHRSWORK1=40)["multi_job_proxy"] == 1


def test_mjp_moderate_secondary():
    # 20 hrs differential → band 15–34 → code 2
    assert _row(EMPSTAT=10, UHRSWORKT=60, UHRSWORK1=40)["multi_job_proxy"] == 2


def test_mjp_heavy_secondary():
    # 35+ hrs → code 3
    assert _row(EMPSTAT=10, UHRSWORKT=75, UHRSWORK1=40)["multi_job_proxy"] == 3


def test_mjp_excluded_when_not_at_work():
    # EMPSTAT not in {10,11,12} → valid_hrs = False → proxy = 0
    assert _row(EMPSTAT=33, UHRSWORKT=50, UHRSWORK1=40)["multi_job_proxy"] == 0


def test_mjp_excluded_on_niu_code():
    # Hours-vary / NIU codes ≥ 997 are excluded
    assert _row(EMPSTAT=10, UHRSWORKT=997, UHRSWORK1=40)["multi_job_proxy"] == 0


# ---------------------------------------------------------------------------
# apply_exclusions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# housing (SPMMORT)
# ---------------------------------------------------------------------------

def test_housing_owner_with_mortgage():
    assert _row(SPMMORT=1)["housing"] == 0

def test_housing_owner_free_clear():
    assert _row(SPMMORT=2)["housing"] == 1

def test_housing_renter():
    assert _row(SPMMORT=3)["housing"] == 2

def test_housing_niu_is_na():
    assert _row(SPMMORT=9)["housing"] == 3


# ---------------------------------------------------------------------------
# metro METFIPS fix
# ---------------------------------------------------------------------------

def test_metro_cbsa_code_is_metro():
    assert _row(METFIPS=14460)["metro"] == 1  # Boston CBSA

def test_metro_99998_is_nonmetro():
    assert _row(METFIPS=99998)["metro"] == 0


# ---------------------------------------------------------------------------
# apply_exclusions
# ---------------------------------------------------------------------------

def test_apply_exclusions_drops_missing_income():
    hh = pd.DataFrame([
        {"HHINCOME": 50_000},
        {"HHINCOME": None},
        {"HHINCOME": 80_000},
    ])
    derived = pd.DataFrame({"topcoded": [0, 0, 0]})
    hh_out, der_out = pp.apply_exclusions(hh, derived)
    assert len(der_out) == 2
    assert len(hh_out) == 2


def test_apply_exclusions_keeps_negative_income():
    hh = pd.DataFrame([{"HHINCOME": -1_000}, {"HHINCOME": 50_000}])
    derived = pd.DataFrame({"topcoded": [0, 0]})
    _, der_out = pp.apply_exclusions(hh, derived)
    assert len(der_out) == 2


def test_apply_exclusions_all_missing_yields_empty():
    hh = pd.DataFrame([{"HHINCOME": None}, {"HHINCOME": None}])
    derived = pd.DataFrame({"topcoded": [0, 0]})
    _, der_out = pp.apply_exclusions(hh, derived)
    assert len(der_out) == 0
