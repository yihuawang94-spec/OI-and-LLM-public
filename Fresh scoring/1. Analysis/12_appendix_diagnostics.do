* Appendix summaries. Raw-scale prediction error is separate from standardized associations.
* No additional regressions or model comparisons are introduced here.
use "$main_data/main_summary.dta", clear
export delimited using "$appendix/Supplement_A3_association_stability.csv", replace
use "$main_data/main_by_run.dta", clear
export delimited using "$appendix/Supplement_A3_individual_iterations.csv", replace

foreach result in oi turnover {
    use "$main_data/main_summary.dta", clear
    if "`result'" == "oi" keep if outcome == "Human OI"
    if "`result'" == "turnover" keep if outcome == "Turnover intentions"
    sort n_examples
    gen str20 rowname = "OILLM_" + strtrim(string(n_examples))
    replace rowname = "NIS" if n_examples == -1
    mkmat primary_r primary_r_p run_r_mean run_r_sd run_r_min run_r_max, matrix(R) rownames(rowname)
    esttab matrix(R, fmt(3)) using "$appendix/Supplement_A3_`result'_correlation_stability.rtf", replace ///
        collabels("Averaged scores" "Pearson p" "Mean iteration" "SD" "Minimum" "Maximum") ///
        title("Correlation stability: `result'") nomtitles nonumbers nogaps ///
        note("The first column correlates averaged participant scores with the outcome." ///
             "Pearson p is the conventional correlation test; remaining columns describe iterations on the same people. NIS is fixed.") ///
    substitute("\fs24" "\fs20")
    mkmat primary_beta primary_p run_beta_mean run_beta_sd run_beta_min run_beta_max run_p_min run_p_max, matrix(B) rownames(rowname)
    esttab matrix(B, fmt(3)) using "$appendix/Supplement_A3_`result'_beta_stability.rtf", replace ///
        collabels("Averaged beta" "p" "Mean beta" "SD" "Min beta" "Max beta" "Min p" "Max p") ///
        title("Fully controlled standardized coefficient stability: `result'") nomtitles nonumbers nogaps ///
        note("All models use the same participants, demographics and six Glassdoor correlates." ///
             "SD and ranges describe scoring variation conditional on this sample and the fixed example banks." ///
             "They are not confidence intervals for a population coefficient; p-values are not averaged.") ///
    substitute("\fs24" "\fs20")
}

* Participant-level variation in the variable itself, on its original 1-7 scale.
use "$data/Prolific_iterations.dta", clear
collapse (mean) score_mean=oi_full (sd) score_sd=oi_full ///
    (min) score_min=oi_full (max) score_max=oi_full (count) iterations=oi_full, ///
    by(participant_id n_examples)
gen double score_range = score_max - score_min
label var score_sd "Within-person SD across selected scoring iterations"
save "$main_data/participant_score_stability.dta", replace
collapse (mean) mean_score=score_mean mean_person_sd=score_sd mean_person_range=score_range ///
    (sd) between_person_sd=score_mean (max) max_person_sd=score_sd max_person_range=score_range ///
    (count) participants=score_mean, by(n_examples)
export delimited using "$appendix/Supplement_A3_score_stability.csv", replace
gen str20 rowname = "OILLM_" + strtrim(string(n_examples))
mkmat mean_score between_person_sd mean_person_sd max_person_sd mean_person_range max_person_range, matrix(S) rownames(rowname)
esttab matrix(S, fmt(3)) using "$appendix/Supplement_A3_score_stability.rtf", replace ///
    collabels("Mean score" "Between-person SD" "Mean within-person SD" "Max within-person SD" "Mean range" "Max range") ///
    title("Variation in OILLM scores across participants and iterations") nomtitles nonumbers nogaps ///
    note("Scores use the original 1-7 scale. Between-person SD uses each participant's averaged score." ///
         "Within-person SD describes repeated scoring of the same text; it is missing when only one iteration is selected.") ///
    substitute("\fs24" "\fs20")

* Raw-scale errors for each individual iteration.
use "$data/Prolific_iterations.dta", clear
gen double signed_error = oi_full - w2_oi_mean
gen double absolute_error = abs(signed_error)
gen double squared_error = signed_error^2
collapse (mean) mae=absolute_error bias=signed_error mse=squared_error ///
    (count) N=absolute_error, by(n_examples run)
gen double rmse = sqrt(mse)
save "$main_data/MAE_by_iteration.dta", replace
export delimited using "$appendix/Supplement_A4_MAE_individual_iterations.csv", replace
collapse (mean) iteration_mae_mean=mae (sd) iteration_mae_sd=mae ///
    (min) iteration_mae_min=mae (max) iteration_mae_max=mae, by(n_examples)
tempfile iteration_error
save `iteration_error'

capture postclose mae_results
postfile mae_results int n_examples N double mae bias rmse using "$main_data/MAE_averaged_scores.dta", replace
use "$data/Prolific_clean.dta", clear
foreach examples in 0 30 100 200 300 {
    gen double error_`examples' = full_`examples' - w2_oi_mean
    gen double abs_`examples' = abs(error_`examples')
    gen double sq_`examples' = error_`examples'^2
    quietly summarize abs_`examples', meanonly
    scalar mae_n = r(N)
    scalar mae_value = r(mean)
    quietly summarize error_`examples', meanonly
    scalar mae_bias = r(mean)
    quietly summarize sq_`examples', meanonly
    post mae_results (`examples') (mae_n) (mae_value) (mae_bias) (sqrt(r(mean)))
}
postclose mae_results
use "$main_data/MAE_averaged_scores.dta", clear
merge 1:1 n_examples using `iteration_error', assert(3) nogen
save "$main_data/MAE_summary.dta", replace
export delimited using "$appendix/Supplement_A4_MAE.csv", replace
gen str20 rowname = "OILLM_" + strtrim(string(n_examples))
mkmat mae bias rmse iteration_mae_mean iteration_mae_sd iteration_mae_min iteration_mae_max, matrix(M) rownames(rowname)
esttab matrix(M, fmt(3)) using "$appendix/Supplement_A4_MAE.rtf", replace ///
    collabels("MAE averaged" "Signed error" "RMSE averaged" "Mean iteration MAE" "SD" "Min MAE" "Max MAE") ///
    title("Prediction error against self-reported OI") nomtitles nonumbers nogaps ///
    note("MAE is mean absolute error, in points on the 1-7 OI scale; signed error is predicted minus human OI." ///
         "The first three columns evaluate averaged scores; the remaining columns describe separate iterations." ///
         "NIS is omitted because its native units are not 1-7 OI points. Error and adjusted association assess different properties.") ///
    substitute("\fs24" "\fs20")
use "$data/Prolific_clean.dta", clear
