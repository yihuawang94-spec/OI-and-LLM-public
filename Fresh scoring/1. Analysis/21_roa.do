* Current fixed-effects specification. Called by master.do; no A-B models.
* ---- Data and settings ---------------------------------------------------
use "$archival_data/analysis_nis_roa.dta", clear
isid rcid year
xtset rcid year

glo DV         "roa_lead1_w"
glo NIS        "nis_1"
glo controls   "$roa_controls"
glo correlates "overall_rating rating_culture recommend ceo_approval seniority_mean tenure_mean"

* Remove roa_w from controls to omit prior ROA from Models 2-3.
* To use raw ROA, change DV to roa_lead1 and roa_w in controls to roa.

confirm numeric variable rcid year roa roa_lead1 $NIS cs_at cs_lt ///
    n_nis_reviews headcount_avg turnover hiring_rate $correlates
confirm string variable naics_code

* Keep the stored lead: Python matched accounts ending in calendar year t+1.
* Rebuilding it with F.roa would lose leads when next year's review row is
* absent. For non-December year-ends, the fiscal period can overlap year t.
* ROA = net income / year-end total assets; .05 means 5 percent.
* The input DTA is never overwritten by this file.
label var roa       "ROA, current account-end year (fraction)"
label var roa_lead1 "ROA, next account-end year (fraction)"

* ---- Industry identifier for the fixed effects --------------------------
* As in the turnover file, firms without a NAICS code share an unknown group.
capture drop naics_id
capture drop naics_fe
gen naics_fe = naics_code
replace naics_fe = "unknown" if naics_fe == ""
encode naics_fe, gen(naics_id)
drop naics_fe

* ---- Controls and labels ------------------------------------------------
capture drop ln_assets
capture drop leverage
capture drop ln_reviews
gen double ln_assets = ln(cs_at) if cs_at > 0 & cs_at < .
gen double leverage  = cs_lt / cs_at if cs_at > 0 & cs_at < .
gen double ln_reviews = ln(n_nis_reviews) if n_nis_reviews > 0 & n_nis_reviews < .

label var nis_1          "Net ingroup share (NIS)"
label var ln_assets      "Log total assets"
label var leverage       "Total liabilities / total assets"
label var ln_reviews     "Log review count"
label var headcount_avg  "Average headcount"
label var turnover       "Current-year external turnover"
label var hiring_rate    "Hiring rate"
label var overall_rating "Overall rating"
label var rating_culture "Culture and values"
label var recommend      "Recommend to a friend"
label var ceo_approval   "CEO approval"
label var seniority_mean "Seniority"
label var tenure_mean    "Tenure"

* ---- ROA winsorisation --------------------------------------------------
* Calculate cutoffs once, using all nonmissing current-year ROA observations
* in the input file, BEFORE the firm selection or any regression exclusions.
* This preserves the cutoffs used in the recent 30_archival_main.do file.
* Use the same cutoffs for current and next-year ROA. No other variables are
* winsorised, and missing values stay missing.
quietly count if !missing(roa)
if r(N) < 2 {
    display as error "Too few nonmissing ROA observations. Check analysis_nis_roa.dta."
    exit 2000
}
quietly _pctile roa if !missing(roa), percentiles($roa_p_low $roa_p_high)
scalar roa_p1 = r(r1)
scalar roa_p99 = r(r2)

capture drop roa_w
gen double roa_w = roa
replace roa_w = roa_p1 if roa < roa_p1 & roa < .
replace roa_w = roa_p99 if roa > roa_p99 & roa < .

capture drop roa_lead1_w
gen double roa_lead1_w = roa_lead1
replace roa_lead1_w = roa_p1 if roa_lead1 < roa_p1 & roa_lead1 < .
replace roa_lead1_w = roa_p99 if roa_lead1 > roa_p99 & roa_lead1 < .

label var roa_w       "Prior ROA, winsorised"
label var roa_lead1_w "Next-year ROA, winsorised"
display as text "ROA cutoffs (pooled current-year $roa_p_low% / $roa_p_high%): " ///
    as result roa_p1 " / " roa_p99

* ---- Table rows: fixed effects and NIS association ------------------------
* Coefficient x sample SD of NIS x 100 gives ROA percentage points.
* This row is not an outcome-standardised beta.
capture program drop ladder_rows
program define ladder_rows
    estadd local fe_year "Yes"
    estadd local fe_ind  "Yes"
    estadd local fe_firm "Yes"
    quietly summarize $NIS if e(sample)
    estadd scalar sd_effect = _b[$NIS] * r(sd) * 100
end

* ---- Review-count rule --------------------------------------------------
* Apply the review-count requirement separately to each firm-year.

keep if n_nis_reviews >= $min_reviews & n_nis_reviews < .

*==========================================================================
* STEP 1. Main analysis.
* Each model uses its own available observations, as in the turnover file.
*==========================================================================

save "$archival_data/ROA_clean.dta", replace

eststo clear

* (1) NIS alone
reghdfe $DV $NIS, absorb(year naics_id rcid) vce(cluster rcid)
ladder_rows
eststo m1

* (2) + controls, including prior ROA under the default settings
reghdfe $DV $NIS $controls, absorb(year naics_id rcid) vce(cluster rcid)
ladder_rows
eststo m2

* (3) + six Glassdoor correlates
reghdfe $DV $NIS $controls $correlates, absorb(year naics_id rcid) vce(cluster rcid)
ladder_rows
eststo m3

*==========================================================================
* STEP 2. Tables.
* All control coefficients appear in the regression output and the log.
*==========================================================================

* Console
esttab m1 m2 m3, label b(%9.3f) se(%9.3f) ///
    star(* 0.05 ** 0.01 *** 0.001) ///
    keep($NIS) order($NIS) ///
    mtitles("NIS only" "+ Controls" "+ Six correlates") ///
    stats(fe_year fe_ind fe_firm sd_effect N N_clust r2_a r2_within, ///
        labels("Year FE" "Industry FE" "Firm FE" "1-SD NIS association (pp ROA)" ///
            "Firm-years" "Firms" "Adjusted R-squared" "Within R-squared") ///
        fmt(0 0 0 3 %9.0fc %9.0fc 3 3)) ///
    nogaps

* Word (RTF)
esttab m1 m2 m3 using "$appendix/Table_A6_2_ROA.rtf", replace ///
    label b(%9.3f) se(%9.3f) star(* 0.05 ** 0.01 *** 0.001) ///
    keep($NIS) order($NIS) ///
    mtitles("NIS only" "+ Controls" "+ Six correlates") ///
    stats(fe_year fe_ind fe_firm sd_effect N N_clust r2_a r2_within, ///
        labels("Year FE" "Industry FE" "Firm FE" "1-SD NIS association (pp ROA)" ///
            "Firm-years" "Firms" "Adjusted R-squared" "Within R-squared") ///
        fmt(0 0 0 3 %9.0fc %9.0fc 3 3)) ///
    title("Table A6.2. NIS and next-year return on assets") ///
    note("DV: $DV. ROA is net income / year-end total assets for a fiscal year ending in t+1." ///
        "NIS and all controls enter in year t. Non-December fiscal periods can overlap the review year." ///
        "All models include firm, year and industry fixed effects; SEs are clustered by firm." ///
        "Models 2-3 controls: $controls. Model 3 adds the six Glassdoor correlates." ///
        "Variables with suffix _w use pooled current-year ROA $roa_p_low%/$roa_p_high% cutoffs, calculated before sample selection." ///
        "Each included firm-year has at least $min_reviews usable reviews." ///
        "Each model uses its own available observations. No headcount cutoff is applied." ///
        "The 1-SD association is in ROA percentage points; it uses each model's sample SD of NIS.") ///
    nogaps ///
    substitute("\fs24" "\fs20")
