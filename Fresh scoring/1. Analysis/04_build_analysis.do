* Prepare every analysis variable before fitting regressions.
* Run via master.do. All originals and saved scores remain unchanged.
use "$data/Prolific_analysis.dta", clear
confirm string variable participant_id
isid participant_id
assert _N == $comparison_N
confirm numeric variable w2_oi_mean w2_turnover_mean nis_all_text gender ethnicity age $correlates
foreach var of varlist w2_oi_mean w2_turnover_mean nis_all_text gender ethnicity age $correlates {
    assert !missing(`var')
}
foreach alias in oi turnover NIS {
    capture drop `alias'
}
generate double oi = w2_oi_mean
generate double turnover = w2_turnover_mean
generate double NIS = nis_all_text
egen double z_oi = std(oi)
egen double z_turnover = std(turnover)
egen double z_nis = std(NIS)
assert !missing(z_oi, z_turnover, z_nis)
label variable oi "Human OI: wave-2 six-item mean"
label variable turnover "Human turnover intentions: wave-2 mean"
label variable NIS "NIS: all review text"
label variable z_oi "Human OI, standardised"
label variable z_turnover "Turnover intentions, standardised"
label variable z_nis "NIS, standardised"
foreach var of varlist $correlates {
    egen double z_`var' = std(`var')
    quietly summarize `var'
    if r(sd) == 0 replace z_`var' = 0
    assert !missing(z_`var')
}
fvset base 1 gender ethnicity
tempfile main_people main_run_scores main_mean_scores main_run_summary
save `main_people'

use "$data/Scoring/scores_long.dta", clear
confirm string variable participant_id protocol_hash source_protocol_hash model_version transport_layout endpoint_location
confirm numeric variable n_examples run fold_id bank_set bank_repeat status_complete run_complete ///
    oi_full oi_item1 oi_item2 oi_item3 oi_item4 oi_item5 oi_item6
assert inlist(n_examples, 0, 30, 100, 200, 300)
assert run >= 1 & run == floor(run) & !missing(run)

* Select each condition's requested prefix BEFORE checking completeness.
* Higher, unselected runs may be missing without affecting this analysis.
generate int runs_selected = .
foreach examples in 0 30 100 200 300 {
    local requested : copy global main_runs_`examples'
    replace runs_selected = `requested' if n_examples == `examples'
}
keep if run <= runs_selected
isid participant_id n_examples run
foreach examples in 0 30 100 200 300 {
    local requested : copy global main_runs_`examples'
    forvalues r = 1/`requested' {
        quietly count if n_examples == `examples' & run == `r' & status_complete == 1
        if r(N) != $comparison_N {
            display as error "`examples' examples, run `r': " r(N) " / $comparison_N accepted scores."
            display as error "Export completed scores again, or lower this condition's iteration count in settings.json and re-export."
            capture log close oi_master
            exit 459
        }
        quietly count if n_examples == `examples' & run == `r'
        assert r(N) == $comparison_N
    }
}
assert _N == $comparison_N * ($main_runs_0 + $main_runs_30 + $main_runs_100 + $main_runs_200 + $main_runs_300)
assert status_complete == 1 & run_complete == 1
assert inrange(fold_id, 1, 5) & fold_id == floor(fold_id)
bysort participant_id: assert fold_id == fold_id[1]
assert bank_set == 0 & bank_repeat == 0 if n_examples == 0
assert bank_set == 1 & bank_repeat == run if n_examples > 0
assert !missing(protocol_hash) & !missing(source_protocol_hash) & !missing(model_version) & !missing(transport_layout)
assert protocol_hash == protocol_hash[1]
assert model_version == model_version[1]
global main_model_version = model_version[1]
global main_protocol_hash = protocol_hash[1]
* Source hashes may differ between original responses and the cached extension.
* The Python exporter verifies the fixed example IDs and their order.
* Example means include original run 1 and explicitly cached runs 2 onward.
* The source-details dataset records these request layouts and endpoints.
foreach item in 1 2 3 4 5 6 {
    assert inrange(oi_item`item', 1, 7) & oi_item`item' == floor(oi_item`item')
}
assert inrange(oi_full, 1, 7)
egen double main_check_mean = rowmean(oi_item1 oi_item2 oi_item3 oi_item4 oi_item5 oi_item6)
assert abs(main_check_mean - oi_full) < .000001
drop main_check_mean

merge m:1 participant_id using `main_people', assert(3) nogen
fvset base 1 gender ethnicity
bysort n_examples run: egen double z_proxy = std(oi_full)
assert !missing(z_proxy)
label variable z_proxy "Individual-run OI, standardised within condition and run"
label variable runs_selected "Number of consecutive runs selected for this condition"
sort n_examples run participant_id
label data "OI main analysis: complete selected runs, same 446 people"
save "$data/Prolific_iterations.dta", replace

* Keep source details for later checking; cache names may change after restarts.
preserve
collapse (count) n_predictions=oi_full, by(n_examples run model_version ///
    protocol_hash source_protocol_hash transport_layout endpoint_location)
save "$main_data/main_score_sources.dta", replace
restore

* Raw individual-run scores, one column per condition/run.
preserve
keep participant_id n_examples run oi_full
generate str24 score_id = strtrim(string(n_examples, "%9.0f")) + "_r" + strtrim(string(run, "%9.0f"))
rename oi_full full_
keep participant_id score_id full_
reshape wide full_, i(participant_id) j(score_id) string
save `main_run_scores'
restore

* Completeness checks above ensure these averages always use exactly R scores.
preserve
collapse (mean) full_=oi_full (count) runs_=oi_full, by(participant_id n_examples)
reshape wide full_ runs_, i(participant_id) j(n_examples)
save `main_mean_scores'
restore

use `main_people', clear
merge 1:1 participant_id using `main_mean_scores', assert(3) nogen
merge 1:1 participant_id using `main_run_scores', assert(3) nogen
assert _N == $comparison_N
foreach examples in 0 30 100 200 300 {
    local requested : copy global main_runs_`examples'
    assert runs_`examples' == `requested'
    assert inrange(full_`examples', 1, 7)
    egen double z_full_`examples' = std(full_`examples')
    assert !missing(z_full_`examples')
    label variable full_`examples' "OI: `examples' examples, mean of first `requested' runs"
    label variable z_full_`examples' "OI: `examples' examples, standardised averaged score"
    label variable runs_`examples' "Runs averaged for `examples' examples"
    forvalues r = 1/`requested' {
        assert inrange(full_`examples'_r`r', 1, 7)
        egen double z_full_`examples'_r`r' = std(full_`examples'_r`r')
        assert !missing(z_full_`examples'_r`r')
        label variable full_`examples'_r`r' "OI: `examples' examples, run `r'"
        label variable z_full_`examples'_r`r' "OI: `examples' examples, run `r', standardised"
    }
}
fvset base 1 gender ethnicity
order participant_id oi turnover NIS full_0 full_30 full_100 full_200 full_300 ///
    z_oi z_turnover z_nis z_full_0 z_full_30 z_full_100 z_full_200 z_full_300 ///
    gender ethnicity age $correlates $z_correlates runs_0 runs_30 runs_100 runs_200 runs_300
label data "OI analysis: human measures, NIS, all selected iterations, averages and controls"
save "$data/Prolific_clean.dta", replace
display as result "All participant variables saved: $data/Prolific_clean.dta"

