* Current fixed-effects specification. Called by master.do; no A-B models.
* ---- Data and settings ---------------------------------------------------
use "$archival_data/analysis_nis_turnover.dta", clear
isid rcid year
xtset rcid year

glo NIS        "nis_1"
glo controls   "$turnover_controls"
glo correlates "overall_rating rating_culture recommend ceo_approval seniority_mean tenure_mean"

confirm numeric variable rcid year turnover turnover_lead1 $NIS hiring_rate ///
    headcount_avg n_nis_reviews $correlates
confirm string variable naics_code

* Keep the stored lead: Python matched workforce data for calendar year t+1.
* Rebuilding it with F.turnover would lose leads when next year's review row
* is absent. The input DTA is never overwritten by this file.
label var turnover_lead1 "Next-year external turnover (fraction)"

* ---- Industry identifier for the fixed effects --------------------------
* As in the earlier file, firms without a NAICS code share an unknown group.
capture drop naics_id
capture drop naics_fe
gen naics_fe = naics_code
replace naics_fe = "unknown" if naics_fe == ""
encode naics_fe, gen(naics_id)
drop naics_fe

* ---- Controls and labels ------------------------------------------------
capture drop ln_headcount
capture drop ln_reviews
gen ln_headcount = ln(headcount_avg)
gen ln_reviews   = ln(n_nis_reviews)

label var nis_1          "Net ingroup share (NIS)"
label var hiring_rate    "Hiring rate"
label var ln_headcount   "Log headcount"
label var ln_reviews     "Log review count"
label var overall_rating "Overall rating"
label var rating_culture "Culture and values"
label var recommend      "Recommend to a friend"
label var ceo_approval   "CEO approval"
label var seniority_mean "Seniority"
label var tenure_mean    "Tenure"

* ---- Table rows: fixed effects and NIS association ------------------------
* Coefficient x sample SD of NIS x 100 gives turnover percentage points.
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
* Each model uses its own available observations, as in the earlier file.
*==========================================================================

save "$archival_data/Turnover_clean.dta", replace

eststo clear

* (1) NIS alone
reghdfe turnover_lead1 $NIS, absorb(year naics_id rcid) vce(cluster rcid)
ladder_rows
eststo m1

* (2) + controls
reghdfe turnover_lead1 $NIS $controls, absorb(year naics_id rcid) vce(cluster rcid)
ladder_rows
eststo m2

* (3) + six Glassdoor correlates
reghdfe turnover_lead1 $NIS $controls $correlates, absorb(year naics_id rcid) vce(cluster rcid)
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
        labels("Year FE" "Industry FE" "Firm FE" "1-SD NIS association (pp)" ///
            "Firm-years" "Firms" "Adjusted R-squared" "Within R-squared") ///
        fmt(0 0 0 3 %9.0fc %9.0fc 3 3)) ///
    nogaps

* Word (RTF)
esttab m1 m2 m3 using "$appendix/Table_A6_1_actual_turnover.rtf", replace ///
    label b(%9.3f) se(%9.3f) star(* 0.05 ** 0.01 *** 0.001) ///
    keep($NIS) order($NIS) ///
    mtitles("NIS only" "+ Controls" "+ Six correlates") ///
    stats(fe_year fe_ind fe_firm sd_effect N N_clust r2_a r2_within, ///
        labels("Year FE" "Industry FE" "Firm FE" "1-SD NIS association (pp)" ///
            "Firm-years" "Firms" "Adjusted R-squared" "Within R-squared") ///
        fmt(0 0 0 3 %9.0fc %9.0fc 3 3)) ///
    title("Table A6.1. NIS and next-year actual turnover") ///
    note("DV: external turnover in year t+1. NIS and all controls enter in year t." ///
        "All models include firm, year and industry fixed effects; SEs are clustered by firm." ///
        "Models 2-3 controls: $turnover_controls." ///
        "Model 3 adds overall rating, culture, recommendation, CEO approval, seniority and tenure." ///
        "Each included firm-year has at least $min_reviews usable reviews." ///
        "Each model uses its own available observations. No headcount cutoff is applied." ///
        "The 1-SD association is in turnover percentage points; it uses each model's sample SD of NIS.") ///
    nogaps ///
    substitute("\fs24" "\fs20")
