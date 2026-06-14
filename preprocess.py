#!/usr/bin/env python3
"""
CPS ASEC preprocessor.
Reads IPUMS fixed-width .dat.gz + .xml DDI, derives variables,
writes Arrow files and precomputed stats to docs/data/.

Usage:
    python preprocess.py --input cps_00004.dat.gz --ddi cps_00004.xml --output-dir ./docs/data
"""

import argparse
import gzip
import json
import sys
import time
import xml.etree.ElementTree as ET
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc


# ---------------------------------------------------------------------------
# Topcode thresholds by YEAR (survey year, not income year).
# HHINCOME is censored at this value in the raw data.
# Verify at: https://cps.ipums.org/cps/topcodes_tables.shtml
# ---------------------------------------------------------------------------
TOPCODES = {
    2023: 2099997,   # ASEC 2023, income year 2022
    2024: 2099997,   # ASEC 2024, income year 2023
    2025: 2099997,   # ASEC 2025, income year 2024
}

# Survey year → display label (income year = survey year - 1)
YEAR_LABELS = {2023: "2022", 2024: "2023", 2025: "2024"}
YEAR_CODES  = {2023: 0,      2024: 1,      2025: 2}

WKSWORK2_MIDPOINTS = {1: 7, 2: 20, 3: 33, 4: 43, 5: 48, 6: 51, 0: 0}

FIPS_NAMES = {
    1:"Alabama",2:"Alaska",4:"Arizona",5:"Arkansas",6:"California",
    8:"Colorado",9:"Connecticut",10:"Delaware",11:"District of Columbia",
    12:"Florida",13:"Georgia",15:"Hawaii",16:"Idaho",17:"Illinois",
    18:"Indiana",19:"Iowa",20:"Kansas",21:"Kentucky",22:"Louisiana",
    23:"Maine",24:"Maryland",25:"Massachusetts",26:"Michigan",
    27:"Minnesota",28:"Mississippi",29:"Missouri",30:"Montana",
    31:"Nebraska",32:"Nevada",33:"New Hampshire",34:"New Jersey",
    35:"New Mexico",36:"New York",37:"North Carolina",38:"North Dakota",
    39:"Ohio",40:"Oklahoma",41:"Oregon",42:"Pennsylvania",44:"Rhode Island",
    45:"South Carolina",46:"South Dakota",47:"Tennessee",48:"Texas",
    49:"Utah",50:"Vermont",51:"Virginia",53:"Washington",54:"West Virginia",
    55:"Wisconsin",56:"Wyoming",
}


def step(n, total, msg):
    print(f"[{n}/{total}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# DDI parser — extracts column specs from IPUMS XML codebook
# ---------------------------------------------------------------------------
def parse_ddi(ddi_path):
    """
    Returns list of dicts: {name, start, width, dcml}
    start is 0-based (DDI uses 1-based StartPos).
    """
    tree = ET.parse(ddi_path)
    root = tree.getroot()
    ns = {"ddi": "ddi:codebook:2_5"}

    cols = []
    for var in root.findall(".//ddi:var", ns):
        name = var.get("name")
        dcml = int(var.get("dcml", "0"))
        locs = var.findall("ddi:location", ns)
        if not locs:
            continue
        # Most vars have one location; REPWTP group has many — skip the group entry
        if len(locs) == 1:
            loc = locs[0]
            start = int(loc.get("StartPos")) - 1  # convert to 0-based
            width = int(loc.get("width"))
            cols.append({"name": name, "start": start, "width": width, "dcml": dcml})
        else:
            # Multi-location = numbered replicate weight entries — skip the parent
            # The individual REPWTP1..REPWTP160 vars each have their own single-loc entry
            pass

    return cols


# ---------------------------------------------------------------------------
# Fixed-width loader
# ---------------------------------------------------------------------------
def load_fixed_width(dat_path, ddi_path, wanted=None):
    """
    Parse IPUMS .dat.gz using DDI column specs.
    wanted: set of column names to keep (None = all).
    Returns pd.DataFrame.
    """
    print(f"  Parsing DDI: {ddi_path}")
    col_specs = parse_ddi(ddi_path)

    if wanted:
        col_specs = [c for c in col_specs if c["name"] in wanted]

    colspecs = [(c["start"], c["start"] + c["width"]) for c in col_specs]
    names    = [c["name"] for c in col_specs]
    decimals = {c["name"]: c["dcml"] for c in col_specs if c["dcml"] > 0}

    print(f"  Reading {dat_path} ({Path(dat_path).stat().st_size / 1e6:.0f}MB compressed)…")
    opener = gzip.open if str(dat_path).endswith(".gz") else open
    with opener(dat_path, "rt") as fh:
        df = pd.read_fwf(fh, colspecs=colspecs, names=names, header=None, dtype=str)

    # Convert to numeric and apply implied decimal places
    for col in names:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if col in decimals:
            df[col] = df[col] / (10 ** decimals[col])

    print(f"  {len(df):,} person records")
    return df


# ---------------------------------------------------------------------------
# Variables we need (subset to keep memory manageable)
# ---------------------------------------------------------------------------
WANTED = {
    "YEAR", "SERIAL", "PERNUM",
    "ASECWTH",                      # household weight (person row from householder)
    "REGION", "STATEFIP", "METFIPS",
    "HHINCOME",
    "RELATE", "AGE", "SEX", "RACE", "MARST", "NCHILD", "YNGCH",
    "HISPAN", "EDUC",
    "EMPSTAT", "CLASSWKR", "WKSWORK2", "UHRSWORKLY", "UHRSWORKT", "UHRSWORK1",
    "INCTOT", "INCWAGE", "INCBUS", "INCSS", "INCWELFR", "INCDIVID", "INCRENT",
    "SPMMORT",
} | {f"REPWTP{i}" for i in range(1, 161)}


# ---------------------------------------------------------------------------
# Cohabiting detection
# ---------------------------------------------------------------------------
def detect_cohabiting(df):
    step(2, 9, "Cohabiting partner detection…")
    if "RELATE" not in df.columns:
        print("  WARNING: RELATE not found — skipping")
        return set()
    # 1114=unmarried partner (legacy), 1116=opposite-sex, 1117=same-sex (ASEC 2023+)
    partner_mask = df["RELATE"].isin([1113, 1114, 1116, 1117])
    partner_hh = set(df.loc[partner_mask, "SERIAL"].unique())
    print(f"  {len(partner_hh):,} households flagged as cohabiting")
    return partner_hh


# ---------------------------------------------------------------------------
# Collapse to household (keep PERNUM==1 row per SERIAL)
# ---------------------------------------------------------------------------
def detect_roommates(df):
    step(2, 9, "Roommate detection…")
    if "RELATE" not in df.columns:
        print("  WARNING: RELATE not found — skipping")
        return set()
    # 1113=roomer/boarder, 1115=housemate/roommate
    roommate_mask = df["RELATE"].isin([1113, 1115])
    roommate_hh = set(df.loc[roommate_mask, "SERIAL"].unique())
    print(f"  {len(roommate_hh):,} households flagged as having roommates")
    return roommate_hh


def collapse_to_household(df, cohabiting_serials, roommate_serials):
    df = df.sort_values(["SERIAL", "PERNUM"])
    hh = df.groupby("SERIAL", sort=False).first().reset_index()
    hh["_cohabiting"] = hh["SERIAL"].isin(cohabiting_serials)
    hh["_has_roommate"] = hh["SERIAL"].isin(roommate_serials)
    print(f"  {len(hh):,} households after collapse")
    return hh


# ---------------------------------------------------------------------------
# Derive variables
# ---------------------------------------------------------------------------
def derive_variables(hh):
    step(3, 9, "Deriving variables…")
    out = pd.DataFrame()
    out["id"] = np.arange(len(hh), dtype=np.uint32)

    # year code
    out["year"] = hh["YEAR"].map(YEAR_CODES).fillna(0).astype("uint8")

    # income
    hhincome   = pd.to_numeric(hh["HHINCOME"],  errors="coerce").fillna(0)
    incwage    = pd.to_numeric(hh["INCWAGE"],    errors="coerce").fillna(0)
    incbus     = pd.to_numeric(hh.get("INCBUS",   pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    incss      = pd.to_numeric(hh.get("INCSS",    pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    incdivid   = pd.to_numeric(hh.get("INCDIVID", pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    incrent    = pd.to_numeric(hh.get("INCRENT",  pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    incwelfr   = pd.to_numeric(hh.get("INCWELFR", pd.Series(0, index=hh.index)), errors="coerce").fillna(0)

    out["inc"]      = hhincome.clip(lower=0).astype(np.uint32)
    out["wage_inc"] = (incwage + incbus).clip(lower=0).astype(np.uint32)
    # ASECWTH has dcml=4 already applied by loader; use it directly
    out["weight"]   = pd.to_numeric(hh["ASECWTH"], errors="coerce").fillna(1).astype(np.float32)
    out["state"]    = pd.to_numeric(hh["STATEFIP"], errors="coerce").fillna(0).astype("uint8")

    # age_bucket
    age = pd.to_numeric(hh["AGE"], errors="coerce").fillna(0)
    out["age_bucket"] = pd.cut(
        age, bins=[0, 24, 29, 34, 39, 44, 54, 64, 999],
        labels=[0, 1, 2, 3, 4, 5, 6, 7],
    ).astype(float).fillna(7).astype("uint8")  # ages <18 or missing → bucket 7

    # sex
    out["sex"] = pd.to_numeric(hh["SEX"], errors="coerce").fillna(0).astype("uint8")

    # marst
    marst_raw = pd.to_numeric(hh["MARST"], errors="coerce").fillna(0)
    marst = pd.Series(np.zeros(len(hh), dtype=np.uint8), index=hh.index)
    marst[marst_raw == 1]                          = 0  # married spouse present
    marst[hh["_cohabiting"]]                       = 1  # cohabiting (overrides married)
    marst[marst_raw.isin([2, 3, 4])]               = 2  # sep/divorced
    marst[marst_raw == 5]                          = 3  # widowed
    marst[(marst_raw == 6) & ~hh["_cohabiting"]]   = 4  # never married
    out["marst"] = marst.values.astype("uint8")

    # educ
    educ_raw = pd.to_numeric(hh["EDUC"], errors="coerce").fillna(0)
    out["educ"] = pd.cut(
        educ_raw, bins=[-1, 59, 72, 109, 122, 999],
        labels=[0, 1, 2, 3, 4],
    ).astype(float).fillna(0).astype("uint8")

    # region: collapse CPS division codes (11/12=NE, 21/22=MW, 31-33=S, 41/42=W) → 1-4
    region_raw = pd.to_numeric(hh["REGION"], errors="coerce").fillna(0)
    region = pd.Series(np.zeros(len(hh), dtype=np.uint8), index=hh.index)
    region[region_raw.isin([11, 12])]       = 1  # Northeast
    region[region_raw.isin([21, 22])]       = 2  # Midwest
    region[region_raw.isin([31, 32, 33])]   = 3  # South
    region[region_raw.isin([41, 42])]       = 4  # West
    out["region"] = region.values.astype("uint8")

    # metro (METFIPS: 0 = non-metro, >0 = metro FIPS code)
    # METFIPS: real CBSA codes are 10180–~49740; 99998 = non-metro/unidentified
    metfips = pd.to_numeric(hh.get("METFIPS", pd.Series(99998, index=hh.index)), errors="coerce").fillna(99998)
    out["metro"] = ((metfips > 0) & (metfips < 99998)).astype("uint8")

    # kids / youngest_child
    nchild = pd.to_numeric(hh.get("NCHILD", pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    out["kids"] = (nchild > 0).astype("uint8")

    yngch = pd.to_numeric(hh.get("YNGCH", pd.Series(99, index=hh.index)), errors="coerce").fillna(99)
    yc = pd.Series(np.zeros(len(hh), dtype=np.uint8), index=hh.index)
    yc[(nchild > 0) & (yngch <= 4)]              = 1
    yc[(nchild > 0) & (yngch >= 5) & (yngch <= 12)] = 2
    yc[(nchild > 0) & (yngch >= 13)]             = 3
    out["youngest_child"] = yc.values.astype("uint8")

    # work_status
    empstat  = pd.to_numeric(hh["EMPSTAT"],   errors="coerce").fillna(0)
    classwkr = pd.to_numeric(hh.get("CLASSWKR",   pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    wkswork2 = pd.to_numeric(hh.get("WKSWORK2",   pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    uhrs     = pd.to_numeric(hh.get("UHRSWORKLY", pd.Series(0, index=hh.index)), errors="coerce").fillna(0)

    at_work    = empstat.isin([10, 11, 12])
    wage_wkr   = classwkr.isin([21, 22, 23, 24, 25, 26, 27, 28])
    self_emp   = classwkr.isin([13, 14])
    full_year  = wkswork2 >= 5
    full_time  = uhrs >= 35

    ws = pd.Series(np.full(len(hh), 6, dtype=np.uint8), index=hh.index)
    ws[empstat.isin([20, 21, 22])]                           = 4  # unemployed
    ws[empstat.isin(range(30, 37)) & (incss > 0) & (age >= 55)] = 5  # retired
    ws[at_work & full_time & ~full_year]                     = 2  # FT part-year
    ws[at_work & ~full_time]                                 = 3  # part-time
    ws[at_work & full_time & full_year & self_emp]           = 1  # FT self-employed
    ws[at_work & full_time & full_year & wage_wkr]           = 0  # FT wage (highest)
    out["work_status"] = ws.values.astype("uint8")

    # hours_category
    hc = pd.Series(np.full(len(hh), 3, dtype=np.uint8), index=hh.index)
    hc[at_work & (uhrs >= 35)]            = 0
    hc[at_work & (uhrs >= 15) & (uhrs < 35)] = 1
    hc[at_work & (uhrs < 15)]             = 2
    out["hours_category"] = hc.values.astype("uint8")

    # weeks_worked
    out["weeks_worked"] = wkswork2.map(WKSWORK2_MIDPOINTS).fillna(0).astype("uint8")

    # multi_job_proxy from hours differential (UHRSWORKT - UHRSWORK1)
    # Exclude NIU/varies codes (>= 997)
    uhrsworkt = pd.to_numeric(hh.get("UHRSWORKT", pd.Series(999, index=hh.index)), errors="coerce").fillna(999)
    uhrswork1 = pd.to_numeric(hh.get("UHRSWORK1", pd.Series(999, index=hh.index)), errors="coerce").fillna(999)
    valid_hrs = (uhrsworkt < 997) & (uhrswork1 < 997) & at_work
    sec = (uhrsworkt - uhrswork1).clip(lower=0)
    sec = sec.where(valid_hrs, 0)

    out["secondary_hours"] = sec.clip(upper=99).astype("uint8")  # kept for BRR, not in Arrow

    mjp = pd.Series(np.zeros(len(hh), dtype=np.uint8), index=hh.index)
    mjp[valid_hrs & (sec >= 1)  & (sec <= 14)] = 1
    mjp[valid_hrs & (sec >= 15) & (sec <= 34)] = 2
    mjp[valid_hrs & (sec >= 35)]               = 3
    out["multi_job_proxy"] = mjp.values.astype("uint8")

    # income_type
    wage_income = (incwage + incbus).clip(lower=0)
    hhincome_pos = hhincome.clip(lower=1)
    wage_share = wage_income / hhincome_pos

    it = pd.Series(np.full(len(hh), 3, dtype=np.uint8), index=hh.index)  # zero/neg
    it[hhincome > 0]                               = 2  # primarily passive
    it[(hhincome > 0) & (wage_share >= 0.25)]      = 1  # mixed
    it[(hhincome > 0) & (wage_share >= 0.75)]      = 0  # primarily wages
    out["income_type"] = it.values.astype("uint8")

    # topcoded
    year_raw = pd.to_numeric(hh["YEAR"], errors="coerce").fillna(0).astype(int)
    topcode_flag = pd.Series(np.zeros(len(hh), dtype=np.uint8), index=hh.index)
    for yr, thresh in TOPCODES.items():
        topcode_flag[(year_raw == yr) & (hhincome >= thresh)] = 1
    out["topcoded"] = topcode_flag.values.astype("uint8")

    # race_ethnicity
    race   = pd.to_numeric(hh["RACE"],  errors="coerce").fillna(0)
    hispan = pd.to_numeric(hh.get("HISPAN", pd.Series(0, index=hh.index)), errors="coerce").fillna(0)
    re = pd.Series(np.full(len(hh), 4, dtype=np.uint8), index=hh.index)
    re[(race == 100) & (hispan == 0)]          = 0  # white non-Hispanic
    re[(race == 200) & (hispan == 0)]          = 1  # Black non-Hispanic
    re[hispan > 0]                             = 2  # Hispanic any race
    re[(race.isin([651, 652])) & (hispan == 0)] = 3  # Asian non-Hispanic
    out["race_ethnicity"] = re.values.astype("uint8")

    # housing (from SPMMORT: SPM tenure/mortgage status)
    spmmort = pd.to_numeric(
        hh.get("SPMMORT", pd.Series(9, index=hh.index)), errors="coerce"
    ).fillna(9).astype(int)
    housing = pd.Series(np.full(len(hh), 3, dtype=np.uint8), index=hh.index)  # 3=N/A
    housing[spmmort == 1] = 0  # owner with mortgage
    housing[spmmort == 2] = 1  # owner, free & clear
    housing[spmmort == 3] = 2  # renter
    out["housing"] = housing.values.astype("uint8")

    # has_roommate (any household member with RELATE 1113=roomer/boarder or 1115=housemate)
    out["has_roommate"] = hh.get("_has_roommate", pd.Series(0, index=hh.index)).astype("uint8")

    return out, hh  # return hh so we can attach rep weights later


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------
def apply_exclusions(hh, derived):
    hhincome = pd.to_numeric(hh["HHINCOME"], errors="coerce")
    missing_mask  = hhincome.isna()
    neg_mask      = (~missing_mask) & (hhincome < 0)
    topcode_count = (derived["topcoded"] == 1).sum()

    n_missing = missing_mask.sum()
    keep_mask = ~missing_mask

    derived = derived[keep_mask.values].reset_index(drop=True)
    hh_kept = hh[keep_mask.values].reset_index(drop=True)

    print(f"\n  Exclusions:")
    print(f"    Missing HHINCOME:  {int(n_missing):>8,} records dropped")
    print(f"    Negative HHINCOME: {int(neg_mask.sum()):>8,} records kept, flagged")
    print(f"    Topcoded HHINCOME: {int(topcode_count):>8,} records kept, flagged")
    return hh_kept, derived


# ---------------------------------------------------------------------------
# Arrow schema and write
# ---------------------------------------------------------------------------
ARROW_SCHEMA = pa.schema([
    ("id",             pa.uint32()),
    ("inc",            pa.uint32()),
    ("wage_inc",       pa.uint32()),
    ("weight",         pa.float32()),
    ("state",          pa.uint8()),
    ("year",           pa.uint8()),
    ("age_bucket",     pa.uint8()),
    ("sex",            pa.uint8()),
    ("marst",          pa.uint8()),
    ("educ",           pa.uint8()),
    ("region",         pa.uint8()),
    ("metro",          pa.uint8()),
    ("kids",           pa.uint8()),
    ("youngest_child", pa.uint8()),
    ("work_status",    pa.uint8()),
    ("hours_category", pa.uint8()),
    ("weeks_worked",   pa.uint8()),
    ("multi_job_proxy", pa.uint8()),
    ("income_type",    pa.uint8()),
    ("topcoded",       pa.uint8()),
    ("race_ethnicity", pa.uint8()),
    ("housing",        pa.uint8()),
])


def write_arrow(table, path):
    with pa.OSFile(str(path), "wb") as sink:
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)


def df_to_arrow(df):
    return pa.Table.from_pandas(df[ARROW_SCHEMA.names], schema=ARROW_SCHEMA, preserve_index=False)


def write_arrow_files(derived, output_dir):
    step(4, 9, "Writing national.arrow…")
    path = output_dir / "national.arrow"
    write_arrow(df_to_arrow(derived), path)
    print(f"  {len(derived):,} rows, {path.stat().st_size/1e6:.1f}MB")

    step(5, 9, "Writing state files…")
    states_dir = output_dir / "states"
    states_dir.mkdir(exist_ok=True)
    total = 0
    for code in sorted(derived["state"].unique()):
        sub = derived[derived["state"] == code]
        p = states_dir / f"{int(code):02d}.arrow"
        write_arrow(df_to_arrow(sub), p)
        total += p.stat().st_size
    n_states = len(derived["state"].unique())
    print(f"  {n_states} files, {total/1e6:.1f}MB total")


# ---------------------------------------------------------------------------
# Weighted stats helpers
# ---------------------------------------------------------------------------
def w_median(values, weights):
    order = np.argsort(values)
    v, w = np.array(values)[order], np.array(weights)[order]
    cum = np.cumsum(w)
    idx = np.searchsorted(cum, cum[-1] / 2)
    return float(v[min(idx, len(v)-1)])


def w_mean(values, weights):
    return float(np.average(np.array(values, dtype=float), weights=np.array(weights, dtype=float)))


def w_percentile(values, weights, q):
    order = np.argsort(values)
    v, w = np.array(values)[order], np.array(weights)[order]
    cum = np.cumsum(w)
    idx = np.searchsorted(cum, q * cum[-1])
    return float(v[min(idx, len(v)-1)])


def w_std(values, weights):
    mu = w_mean(values, weights)
    v, w = np.array(values, dtype=float), np.array(weights, dtype=float)
    return float(np.sqrt(np.average((v - mu)**2, weights=w)))


def w_share(mask, weights):
    w = np.array(weights, dtype=float)
    m = np.array(mask, dtype=bool)
    s = w.sum()
    return float(w[m].sum() / s) if s > 0 else 0.0


# ---------------------------------------------------------------------------
# BRR SE
# ---------------------------------------------------------------------------
def brr_se_median(values, weights, rep_weights):
    k = 0.5
    theta = w_median(values, weights)
    sq = [(w_median(values, rw) - theta)**2 for rw in rep_weights]
    variance = sum(sq) / (len(sq) * (1 - k)**2)
    return float(np.sqrt(variance))


def brr_se_mean(values, weights, rep_weights):
    k = 0.5
    theta = w_mean(values, weights)
    sq = [(w_mean(values, rw) - theta)**2 for rw in rep_weights]
    variance = sum(sq) / (len(sq) * (1 - k)**2)
    return float(np.sqrt(variance))


# ---------------------------------------------------------------------------
# Cell stats
# ---------------------------------------------------------------------------
def compute_cell(subset, rep_cols=None):
    n = len(subset)
    if n < 50:
        return None
    inc = subset["inc"].values
    w   = subset["weight"].values
    med  = w_median(inc, w)
    mean = w_mean(inc, w)
    p25  = w_percentile(inc, w, 0.25)
    p75  = w_percentile(inc, w, 0.75)
    cell = {
        "n":            int(n),
        "n_rep":        float(w.sum()),
        "med":          int(round(med)),
        "mean":         int(round(mean)),
        "p25":          int(round(p25)),
        "p75":          int(round(p75)),
        "iqr":          int(round(p75 - p25)),
        "sd":           int(round(w_std(inc, w))),
        "multi_job_pct":round(w_share(subset["multi_job_proxy"].values > 0, w), 4),
        "wage_pct":     round(w_share(subset["income_type"].values == 0, w), 4),
        "rel":          3 if n >= 500 else 2 if n >= 200 else 1,
    }
    if rep_cols:
        try:
            rep_ws = [subset[c].values for c in rep_cols]
            se_med  = brr_se_median(inc, w, rep_ws)
            se_mean = brr_se_mean(inc, w, rep_ws)
            cell["se_med"] = int(round(se_med))
            # CI is around the mean, so it must use the mean's SE — not the median's.
            cell["ci_lo"]  = int(round(mean - 1.96 * se_mean))
            cell["ci_hi"]  = int(round(mean + 1.96 * se_mean))
        except Exception:
            pass
    return cell


# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------
DIMS = {
    "age":   ("age_bucket",     list(range(8))),
    "sex":   ("sex",            [1, 2]),
    "marst": ("marst",          list(range(5))),
    "educ":  ("educ",           list(range(5))),
    "region":("region",         [1, 2, 3, 4]),
    "work":  ("work_status",    list(range(7))),
    "itype": ("income_type",    list(range(4))),
    "kids":  ("kids",           [0, 1]),
    "race":  ("race_ethnicity", list(range(5))),
    "mjob":  ("multi_job_proxy", [0, 1, 2, 3]),
    "house": ("housing",         [0, 1, 2]),
}


def build_key(scope, filter_dict):
    parts = [scope] if scope else []
    parts += [f"{k}={v}" for k, v in sorted(filter_dict.items())]
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Precomputed stats
# ---------------------------------------------------------------------------
def compute_precomputed_stats(derived, rep_cols, output_dir):
    step(6, 9, "Computing precomputed stats…")
    stats = {}
    dim_names = list(DIMS.keys())

    def add(scope, df, filt):
        key = build_key(scope, filt)
        cell = compute_cell(df, rep_cols)
        if cell:
            stats[key] = cell

    # national overall
    add("national", derived, {})

    # national 1D
    n1d = 0
    for dn, (col, vals) in DIMS.items():
        for v in vals:
            add("national", derived[derived[col] == v], {dn: v})
            n1d += 1
    print(f"  National 1D: {n1d}")

    # national 2D
    nw = ns = 0
    for d1, d2 in combinations(dim_names, 2):
        c1, v1s = DIMS[d1]; c2, v2s = DIMS[d2]
        for v1 in v1s:
            for v2 in v2s:
                sub = derived[(derived[c1]==v1) & (derived[c2]==v2)]
                cell = compute_cell(sub, rep_cols)
                if cell:
                    stats[build_key("national", {d1:v1, d2:v2})] = cell; nw += 1
                else:
                    ns += 1
    print(f"  National 2D: {nw} written, {ns} suppressed")

    # per-state × age/work/itype
    ns2 = 0
    for code in sorted(derived["state"].unique()):
        sc = int(code)
        sdf = derived[derived["state"] == code]
        add(f"state={sc}", sdf, {})
        for dn in ["age", "work", "itype"]:
            col, vals = DIMS[dn]
            for v in vals:
                sub = sdf[sdf[col] == v]
                cell = compute_cell(sub)  # no BRR for state slices
                if cell:
                    stats[build_key(f"state={sc}", {dn:v})] = cell; ns2 += 1
    print(f"  State slices: {ns2}")

    # per-region 2D
    nr = nrs = 0
    for reg in [1, 2, 3, 4]:
        rdf = derived[derived["region"] == reg]
        add(f"region={reg}", rdf, {})
        for d1, d2 in combinations(dim_names, 2):
            c1, v1s = DIMS[d1]; c2, v2s = DIMS[d2]
            for v1 in v1s:
                for v2 in v2s:
                    sub = rdf[(rdf[c1]==v1) & (rdf[c2]==v2)]
                    cell = compute_cell(sub)
                    if cell:
                        stats[build_key(f"region={reg}", {d1:v1, d2:v2})] = cell; nr += 1
                    else:
                        nrs += 1
    print(f"  Region 2D: {nr} written, {nrs} suppressed")

    print(f"  Total: {len(stats)} cells")

    step(7, 9, "Writing stats_precomputed.json…")
    p = output_dir / "stats_precomputed.json"
    with open(p, "w") as f:
        json.dump(stats, f, separators=(",", ":"))
    print(f"  {p.stat().st_size/1024:.0f}KB")
    return stats


# ---------------------------------------------------------------------------
# Codebook
# ---------------------------------------------------------------------------
def write_codebook(derived, output_dir):
    step(8, 9, "Writing codebook.json…")
    present_fips = {int(c) for c in derived["state"].unique()}
    cb = {
        "topcodes": {str(k): v for k, v in TOPCODES.items()},
        "years": {str(v): lbl for v, lbl in YEAR_LABELS.items()},
        "year_codes": {str(code): str(yr) for yr, code in YEAR_CODES.items()},
        "year_display": {str(code): lbl for yr, (code, lbl) in {y: (YEAR_CODES[y], YEAR_LABELS[y]) for y in YEAR_CODES}.items()},
        "notes": {
            "multijob": "Multiple job holding estimated from hours differential (UHRSWORKT − UHRSWORK1). Captures secondary job hours but cannot count jobs directly. 'Hours vary' responses (≥997) excluded. People with two equal-hours jobs may be undercounted.",
            "cohabiting": "Cohabiting classification uses RELATE=1114 (unmarried partner). Underidentified prior to 2007.",
            "topcodes": "Topcoded incomes censored at the year-specific maximum. See IPUMS topcode tables.",
            "years": "ASEC survey years shown; income data refers to the prior calendar year (ASEC 2023 → income year 2022).",
        },
        "labels": {
            "age_bucket":    {"0":"18–24","1":"25–29","2":"30–34","3":"35–39","4":"40–44","5":"45–54","6":"55–64","7":"65+"},
            "sex":           {"1":"Male","2":"Female"},
            "marst":         {"0":"Married","1":"Cohabiting","2":"Sep./Divorced","3":"Widowed","4":"Never married"},
            "educ":          {"0":"< HS","1":"HS diploma","2":"Some college","3":"Bachelor's","4":"Graduate"},
            "region":        {"1":"Northeast","2":"Midwest","3":"South","4":"West"},
            "work_status":   {"0":"FT wage","1":"FT self-emp","2":"FT part-year","3":"Part-time","4":"Unemployed","5":"Retired","6":"Not working"},
            "income_type":   {"0":"Primarily wages","1":"Mixed","2":"Passive/transfer","3":"Zero/negative"},
            "kids":          {"0":"No children","1":"Kids present"},
            "youngest_child":{"0":"No children","1":"Under 5","2":"5–12","3":"13–17"},
            "hours_category":{"0":"Full-time","1":"Part-time","2":"Marginal","3":"N/A"},
            "race_ethnicity":{"0":"White non-Hisp","1":"Black non-Hisp","2":"Hispanic","3":"Asian non-Hisp","4":"Other/Multiracial"},
            "metro":         {"0":"Non-metro","1":"Metro"},
            "has_roommate":  {"0":"No roommates","1":"Has roommate"},
            "multi_job_proxy":{"0":"No secondary work","1":"1–14 hrs secondary","2":"15–34 hrs secondary","3":"35+ hrs secondary"},
            "housing":       {"0":"Owner w/ mortgage","1":"Owner, free & clear","2":"Renter","3":"N/A"},
        },
        "states": {
            str(k): {"name": v, "file": f"{k:02d}.arrow"}
            for k, v in FIPS_NAMES.items() if k in present_fips
        },
    }
    p = output_dir / "codebook.json"
    with open(p, "w") as f:
        json.dump(cb, f, indent=2)
    print(f"  {p.stat().st_size/1024:.0f}KB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True, help=".dat or .dat.gz file")
    ap.add_argument("--ddi",    required=True, help=".xml DDI codebook")
    ap.add_argument("--output-dir", default="./docs/data")
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "states").mkdir(exist_ok=True)

    t0 = time.time()

    # 1. Load
    step(1, 9, "Loading CPS fixed-width file…")
    df = load_fixed_width(args.input, args.ddi, wanted=WANTED)

    # 2. Cohabiting + roommate detection
    cohabiting = detect_cohabiting(df)
    roommates  = detect_roommates(df)

    # 3. Collapse to household
    hh = collapse_to_household(df, cohabiting, roommates)
    del df  # free memory

    # 4. Derive
    derived, hh = derive_variables(hh)

    # 5. Exclusions
    hh, derived = apply_exclusions(hh, derived)

    # 6. Attach replicate weights to derived for BRR
    rep_cols = [f"REPWTP{i}" for i in range(1, 161) if f"REPWTP{i}" in hh.columns]
    if rep_cols:
        print(f"  Attaching {len(rep_cols)} replicate weight columns for BRR")
        rep_df = hh[rep_cols].reset_index(drop=True)
        derived = pd.concat([derived, rep_df], axis=1)
    else:
        print("  No replicate weights found — BRR SEs will be omitted")
        rep_cols = None

    # 7. Write Arrow
    write_arrow_files(derived, output_dir)

    # 8. Precomputed stats
    compute_precomputed_stats(derived, rep_cols, output_dir)

    # 9. Codebook
    write_codebook(derived, output_dir)

    step(9, 9, f"Done in {time.time()-t0:.0f}s. Output in {output_dir}")


if __name__ == "__main__":
    main()
