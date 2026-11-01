* Main OI and turnover results. Run via master.do after preparing all variables.
* Every substantive regression appears explicitly and uses global controls.
use "$data/Prolific_clean.dta", clear
fvset base 1 gender ethnicity
tempfile main_run_summary

regress z_oi z_nis, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_nis

regress z_oi z_full_0, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_0

regress z_oi z_full_30, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_30

regress z_oi z_full_100, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_100

regress z_oi z_full_200, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_200

regress z_oi z_full_300, vce(robust)
assert e(N) == $comparison_N
estimates store basic_h_300

regress z_turnover z_nis, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_nis

regress z_turnover z_full_0, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_0

regress z_turnover z_full_30, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_30

regress z_turnover z_full_100, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_100

regress z_turnover z_full_200, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_200

regress z_turnover z_full_300, vce(robust)
assert e(N) == $comparison_N
estimates store basic_t_300

*=============================================================================*
* 2. Primary regressions -- explicit commands for standalone use
*=============================================================================*
* full_* and z_full_* refer to the averages prepared above.
* All coefficients describe adjusted associations. They are not percentages of OI.

* Human OI
regress z_oi z_nis $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_nis

regress z_oi z_full_0 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_0

regress z_oi z_full_30 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_30

regress z_oi z_full_100 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_100

regress z_oi z_full_200 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_200

regress z_oi z_full_300 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_h_300

* Turnover intentions
regress z_turnover z_nis $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_nis

regress z_turnover z_full_0 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_0

regress z_turnover z_full_30 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_30

regress z_turnover z_full_100 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_100

regress z_turnover z_full_200 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_200

regress z_turnover z_full_300 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store main_t_300

* Extract stored estimates and correlations. No models are refitted here.
capture postclose main_primary
postfile main_primary int n_examples str24 outcome int N ///
    double correlation correlation_p beta se df_r p ci_low ci_high ///
    using "$main_data/main_primary_estimates.dta", replace
foreach result in h t {
    global main_outcome "Human OI"
    global main_dv z_oi
    if "`result'" == "t" {
        global main_outcome "Turnover intentions"
        global main_dv z_turnover
    }
    foreach method in nis 0 30 100 200 300 {
        global main_coef z_full_`method'
        global main_examples `method'
        if "`method'" == "nis" {
            global main_coef z_nis
            global main_examples -1
        }
        estimates restore main_`result'_`method'
        assert e(N) == $comparison_N
        assert _se[$main_coef] > 0 & _se[$main_coef] < .
        scalar main_p = 2 * ttail(e(df_r), abs(_b[$main_coef] / _se[$main_coef]))
        scalar main_ci_low = _b[$main_coef] - invttail(e(df_r), .025) * _se[$main_coef]
        scalar main_ci_high = _b[$main_coef] + invttail(e(df_r), .025) * _se[$main_coef]
        quietly correlate $main_dv $main_coef if e(sample)
        post main_primary ($main_examples) ("$main_outcome") (e(N)) ///
            (r(rho)) (2*ttail(e(N)-2,abs(r(rho))*sqrt((e(N)-2)/(1-r(rho)^2)))) (_b[$main_coef]) (_se[$main_coef]) (e(df_r)) (main_p) (main_ci_low) (main_ci_high)
    }
}
postclose main_primary

*=============================================================================*
* 3. Individual-run regressions and descriptive ranges
*=============================================================================*
* Correlations and p-value ranges refer to the same outcome as each regression.
* p is the robust regression t-test p-value; correlation_p is the conventional Pearson test.
* Neither kind of p-value is averaged.
capture postclose main_run_models
postfile main_run_models int n_examples run str24 outcome int N ///
    double correlation correlation_p beta se df_r p ci_low ci_high ///
    using "$main_data/main_by_run.dta", replace

capture program drop oi_main_post_run
program define oi_main_post_run
    assert e(N) == $comparison_N
    assert _se[z_proxy] > 0 & _se[z_proxy] < .
    scalar main_p = 2 * ttail(e(df_r), abs(_b[z_proxy] / _se[z_proxy]))
    scalar main_ci_low = _b[z_proxy] - invttail(e(df_r), .025) * _se[z_proxy]
    scalar main_ci_high = _b[z_proxy] + invttail(e(df_r), .025) * _se[z_proxy]
    quietly correlate $main_dv z_proxy if e(sample)
    post main_run_models ($main_examples) ($main_run) ("$main_outcome") (e(N)) ///
        (r(rho)) (2*ttail(e(N)-2,abs(r(rho))*sqrt((e(N)-2)/(1-r(rho)^2)))) (_b[z_proxy]) (_se[z_proxy]) (e(df_r)) (main_p) (main_ci_low) (main_ci_high)
end

use "$data/Prolific_iterations.dta", clear
foreach examples in 0 30 100 200 300 {
    global main_examples `examples'
    local requested : copy global main_runs_`examples'
    forvalues r = 1/`requested' {
        global main_run `r'

        global main_outcome "Human OI"
        global main_dv z_oi
        regress z_oi z_proxy $demographics $z_correlates ///
            if n_examples == $main_examples & run == $main_run, vce(robust)
        oi_main_post_run

        global main_outcome "Turnover intentions"
        global main_dv z_turnover
        regress z_turnover z_proxy $demographics $z_correlates ///
            if n_examples == $main_examples & run == $main_run, vce(robust)
        oi_main_post_run
    }
}
postclose main_run_models
capture program drop oi_main_post_run

use "$main_data/main_by_run.dta", clear
isid n_examples run outcome
assert N == $comparison_N
assert !missing(correlation, beta, se, df_r, p)
generate byte significant = p < .05
generate byte expected_sign = (outcome == "Human OI" & beta > 0) | ///
    (outcome == "Turnover intentions" & beta < 0)
sort n_examples outcome run
save "$main_data/main_by_run.dta", replace

collapse (count) runs_used=run ///
    (mean) run_r_mean=correlation run_beta_mean=beta ///
    (sd) run_r_sd=correlation run_beta_sd=beta ///
    (min) run_r_min=correlation run_rp_min=correlation_p run_beta_min=beta run_p_min=p ///
    (max) run_r_max=correlation run_rp_max=correlation_p run_beta_max=beta run_p_max=p ///
    (sum) n_significant=significant n_expected=expected_sign, by(n_examples outcome)
foreach examples in 0 30 100 200 300 {
    local requested : copy global main_runs_`examples'
    assert runs_used == `requested' if n_examples == `examples'
}
* One selected run has a range but no estimable across-run SD.
replace run_beta_sd = . if runs_used < 2
replace run_r_sd = . if runs_used < 2
save `main_run_summary'

use "$main_data/main_primary_estimates.dta", clear
rename correlation primary_r
rename correlation_p primary_r_p
rename beta primary_beta
rename se primary_se
rename df_r primary_df_r
rename p primary_p
rename ci_low primary_ci_low
rename ci_high primary_ci_high
merge 1:1 n_examples outcome using `main_run_summary'
assert _merge == 1 if n_examples == -1
assert _merge == 3 if n_examples >= 0
drop _merge
replace runs_used = 0 if n_examples == -1
generate str28 condition = cond(n_examples == -1, "NIS", ///
    cond(n_examples == 0, "OILLM_0", strtrim(string(n_examples, "%9.0f")) + " examples"))
label variable n_examples "Example count; -1 = NIS"
label variable primary_r "Correlation using averaged participant scores (NIS fixed)"
label variable primary_beta "Standardised beta using averaged participant scores (NIS fixed)"
label variable primary_p "Robust regression p-value using averaged scores (NIS fixed)"
label variable run_beta_mean "Mean of separately estimated individual-run betas"
label variable run_beta_sd "Sample SD of separately estimated individual-run betas"
label variable run_p_min "Smallest individual-run regression p-value"
label variable run_p_max "Largest individual-run regression p-value"
label variable runs_used "Runs averaged; 0 for fixed NIS benchmark"
sort outcome n_examples
assert _N == 12
isid n_examples outcome
save "$main_data/main_summary.dta", replace

* Compact results for the Stata Results window and this log.
use "$main_data/main_summary.dta", clear
format primary_r primary_beta run_r_min run_r_max run_beta_mean run_beta_sd run_beta_min run_beta_max %8.3f
format primary_p run_p_min run_p_max %10.3g
foreach outcome in "Human OI" "Turnover intentions" {
    display as result "`outcome': primary averaged-score results"
    list condition runs_used primary_r primary_beta primary_p if outcome == "`outcome'", noobs abbreviate(24)
    display as result "`outcome': individual-run ranges and beta SD"
    list condition run_r_min run_r_max run_beta_mean run_beta_sd if outcome == "`outcome'", noobs abbreviate(24)
    list condition run_beta_min run_beta_max run_p_min run_p_max n_significant if outcome == "`outcome'", noobs abbreviate(24)
}


esttab basic_h_nis basic_h_0 basic_h_30 basic_h_100 basic_h_200 basic_h_300 ///
    using "$appendix/Table_A1_human_OI_basic.rtf", replace ///
    rename(z_nis proxy z_full_0 proxy z_full_30 proxy z_full_100 proxy z_full_200 proxy z_full_300 proxy) keep(proxy) coeflabels(proxy "Standardized proxy") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") ///
    star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("NIS" "OILLM_0" "OILLM_30" "OILLM_100" "OILLM_200" "OILLM_300") ///
    stats(N, labels("Participants") fmt(%9.0f)) ///
    title("Table A1. Human OI: unadjusted associations") ///
    note("Outcomes and proxies are standardized. LLM proxies average the selected iterations before standardization." ///
         "Each model contains one proxy and an intercept." ///
         "Robust standard errors in parentheses; two-sided p-values on the following line.") ///
    nonumbers nogaps compress ///
    substitute("\fs24" "\fs20")

esttab main_h_nis main_h_0 main_h_30 main_h_100 main_h_200 main_h_300 ///
    using "$appendix/Table_A2_human_OI_controlled.rtf", replace ///
    rename(z_nis proxy z_full_0 proxy z_full_30 proxy z_full_100 proxy z_full_200 proxy z_full_300 proxy) keep(proxy) coeflabels(proxy "Standardized proxy") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") ///
    star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("NIS" "OILLM_0" "OILLM_30" "OILLM_100" "OILLM_200" "OILLM_300") ///
    stats(N, labels("Participants") fmt(%9.0f)) ///
    title("Table A2. Human OI: fully controlled associations") ///
    note("Outcomes and proxies are standardized. LLM proxies average the selected iterations before standardization." ///
         "Models include gender, ethnicity, age and the six Glassdoor correlates." ///
         "Robust standard errors in parentheses; two-sided p-values on the following line.") ///
    nonumbers nogaps compress ///
    substitute("\fs24" "\fs20")

esttab basic_t_nis basic_t_0 basic_t_30 basic_t_100 basic_t_200 basic_t_300 ///
    using "$appendix/Table_A3_turnover_intentions_basic.rtf", replace ///
    rename(z_nis proxy z_full_0 proxy z_full_30 proxy z_full_100 proxy z_full_200 proxy z_full_300 proxy) keep(proxy) coeflabels(proxy "Standardized proxy") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") ///
    star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("NIS" "OILLM_0" "OILLM_30" "OILLM_100" "OILLM_200" "OILLM_300") ///
    stats(N, labels("Participants") fmt(%9.0f)) ///
    title("Table A3. Turnover intentions: unadjusted associations") ///
    note("Outcomes and proxies are standardized. LLM proxies average the selected iterations before standardization." ///
         "Each model contains one proxy and an intercept." ///
         "Robust standard errors in parentheses; two-sided p-values on the following line.") ///
    nonumbers nogaps compress ///
    substitute("\fs24" "\fs20")

esttab main_t_nis main_t_0 main_t_30 main_t_100 main_t_200 main_t_300 ///
    using "$appendix/Table_A4_turnover_intentions_controlled.rtf", replace ///
    rename(z_nis proxy z_full_0 proxy z_full_30 proxy z_full_100 proxy z_full_200 proxy z_full_300 proxy) keep(proxy) coeflabels(proxy "Standardized proxy") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") ///
    star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("NIS" "OILLM_0" "OILLM_30" "OILLM_100" "OILLM_200" "OILLM_300") ///
    stats(N, labels("Participants") fmt(%9.0f)) ///
    title("Table A4. Turnover intentions: fully controlled associations") ///
    note("Outcomes and proxies are standardized. LLM proxies average the selected iterations before standardization." ///
         "Models include gender, ethnicity, age and the six Glassdoor correlates." ///
         "Robust standard errors in parentheses; two-sided p-values on the following line.") ///
    nonumbers nogaps compress ///
    substitute("\fs24" "\fs20")

global main_x_min 0
global main_x_max 300
global main_beta_oi_min $graph_oi_beta_min
global main_beta_oi_max $graph_oi_beta_max
global main_beta_oi_step $graph_oi_beta_step
global main_beta_turn_min $graph_turnover_beta_min
global main_beta_turn_max $graph_turnover_beta_max
global main_beta_turn_step $graph_turnover_beta_step
global main_r_oi_min $graph_oi_r_min
global main_r_oi_max $graph_oi_r_max
global main_r_oi_step $graph_oi_r_step
global main_r_turn_min $graph_turnover_r_min
global main_r_turn_max $graph_turnover_r_max
global main_r_turn_step $graph_turnover_r_step
global main_graph_scheme s2color
capture findfile scheme-plotplain.scheme
if !_rc {
    global main_graph_scheme plotplain
}

use "$main_data/main_summary.dta", clear
quietly summarize primary_beta if n_examples == -1 & outcome == "Human OI", meanonly
scalar main_nis_human = r(mean)
quietly summarize primary_beta if n_examples == -1 & outcome == "Turnover intentions", meanonly
scalar main_nis_turnover = r(mean)
quietly summarize primary_r if n_examples == -1 & outcome == "Human OI", meanonly
scalar main_nis_r_human = r(mean)
quietly summarize primary_r if n_examples == -1 & outcome == "Turnover intentions", meanonly
scalar main_nis_r_turnover = r(mean)
keep if n_examples >= 0
generate byte plot_summary = 1
generate double nis_beta = main_nis_human if outcome == "Human OI"
replace nis_beta = main_nis_turnover if outcome == "Turnover intentions"
generate double nis_r = main_nis_r_human if outcome == "Human OI"
replace nis_r = main_nis_r_turnover if outcome == "Turnover intentions"
append using "$main_data/main_by_run.dta"
keep if n_examples >= 0
replace plot_summary = 0 if missing(plot_summary)
* Keep runs at their exact example count, including zero-shot at x = 0.
generate double plot_x = n_examples
sort outcome plot_summary n_examples run
quietly count if !inrange(n_examples, $main_x_min, $main_x_max)
if r(N) > 0 {
    display as error "Widen main_x_min / main_x_max in settings.json to include all five example settings."
    exit 198
}

capture program drop oi_main_check_axis
program define oi_main_check_axis
    syntax varlist(numeric), Axis(string) Outcome(string)
    local lower : copy global main_`axis'_min
    local upper : copy global main_`axis'_max
    foreach v of local varlist {
        quietly summarize `v' if outcome == "`outcome'", meanonly
        if r(N) > 0 {
            if r(min) < `lower' - 0.0000001 | r(max) > `upper' + 0.0000001 {
                display as error "Widen main_`axis'_min / main_`axis'_max in settings.json."
                display as error "`outcome': `v' ranges from " r(min) " to " r(max) "."
                exit 198
            }
        }
    }
end
oi_main_check_axis beta primary_beta run_beta_mean nis_beta, axis(beta_oi) outcome("Human OI")
oi_main_check_axis beta primary_beta run_beta_mean nis_beta, axis(beta_turn) outcome("Turnover intentions")
oi_main_check_axis correlation primary_r run_r_mean nis_r, axis(r_oi) outcome("Human OI")
oi_main_check_axis correlation primary_r run_r_mean nis_r, axis(r_turn) outcome("Turnover intentions")
capture program drop oi_main_check_axis


* 3. Human organizational identification: unadjusted Pearson correlations.
twoway ///
    (scatter correlation plot_x if outcome == "Human OI" & plot_summary == 0, ///
        mcolor(gs10) msymbol(Oh) msize(vsmall)) ///
    (connected primary_r n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor("0 114 178") lpattern(solid) lwidth(medthick) ///
        mcolor("0 114 178") msymbol(O) msize(small)) ///
    (connected run_r_mean n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor("213 94 0") lpattern(dash) lwidth(medthick) ///
        mcolor("213 94 0") msymbol(Dh) msize(small)) ///
    (line nis_r n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor(gs7) lpattern(dot) lwidth(medthin)), ///
    xlabel(0 30 100 200 300, ///
        nogrid labsize(small) labcolor(gs5) tlength(1) tlcolor(gs8)) ///
    ylabel($main_r_oi_min($main_r_oi_step)$main_r_oi_max, ///
        nogrid angle(horizontal) labsize(small) labcolor(gs5) ///
        format(%4.2f) tlength(1) tlcolor(gs8)) ///
    xscale(range($main_x_min $main_x_max) noextend lcolor(gs8) lwidth(thin)) ///
    yscale(range($main_r_oi_min $main_r_oi_max) noextend lcolor(gs8) lwidth(thin)) ///
    xtitle("Number of labelled examples", size(small) color(gs3) margin(small)) ///
    ytitle("Correlation (self-reported OI)", size(small) color(gs3) margin(small)) ///
    legend(order(2 "From averaged scores" 3 "Mean across iterations" ///
        1 "Individual iteration" 4 "NIS") ///
        position(6) ring(4) cols(2) size(small) symxsize(7) ///
        region(lstyle(none))) ///
    scheme($main_graph_scheme) ///
    graphregion(color(white) lcolor(white) margin(medium)) ///
    plotregion(color(white) lcolor(none)) ///
    xsize(8) ysize(5.5) name(main_human_oi_correlation, replace)
graph export "$main_output/Figure_A1_human_OI_correlation.png", replace width(2600)
graph export "$main_output/Figure_A1_human_OI_correlation.pdf", replace
graph save "$main_output/Figure_A1_human_OI_correlation.gph", replace

* 1. Human organizational identification: fully controlled standardized betas.
twoway ///
    (scatter beta plot_x if outcome == "Human OI" & plot_summary == 0, ///
        mcolor(gs10) msymbol(Oh) msize(vsmall)) ///
    (connected primary_beta n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor("0 114 178") lpattern(solid) lwidth(medthick) ///
        mcolor("0 114 178") msymbol(O) msize(small)) ///
    (connected run_beta_mean n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor("213 94 0") lpattern(dash) lwidth(medthick) ///
        mcolor("213 94 0") msymbol(Dh) msize(small)) ///
    (line nis_beta n_examples if outcome == "Human OI" & plot_summary == 1, ///
        sort lcolor(gs7) lpattern(dot) lwidth(medthin)), ///
    xlabel(0 30 100 200 300, ///
        nogrid labsize(small) labcolor(gs5) tlength(1) tlcolor(gs8)) ///
    ylabel($main_beta_oi_min($main_beta_oi_step)$main_beta_oi_max, ///
        nogrid angle(horizontal) labsize(small) labcolor(gs5) ///
        format(%4.2f) tlength(1) tlcolor(gs8)) ///
    xscale(range($main_x_min $main_x_max) noextend lcolor(gs8) lwidth(thin)) ///
    yscale(range($main_beta_oi_min $main_beta_oi_max) noextend lcolor(gs8) lwidth(thin)) ///
    xtitle("Number of labelled examples", size(small) color(gs3) margin(small)) ///
    ytitle("Standardized beta (self-reported OI)", size(small) color(gs3) margin(small)) ///
    legend(order(2 "From averaged scores" 3 "Mean across iterations" ///
        1 "Individual iteration" 4 "NIS") ///
        position(6) ring(4) cols(2) size(small) symxsize(7) ///
        region(lstyle(none))) ///
    scheme($main_graph_scheme) ///
    graphregion(color(white) lcolor(white) margin(medium)) ///
    plotregion(color(white) lcolor(none)) ///
    xsize(8) ysize(5.5) name(main_human_oi, replace)
graph export "$main_output/Figure_A2_human_OI_beta.png", replace width(2600)
graph export "$main_output/Figure_A2_human_OI_beta.pdf", replace
graph save "$main_output/Figure_A2_human_OI_beta.gph", replace

* 4. Turnover intentions: unadjusted Pearson correlations.
twoway ///
    (scatter correlation plot_x if outcome == "Turnover intentions" & plot_summary == 0, ///
        mcolor(gs10) msymbol(Oh) msize(vsmall)) ///
    (connected primary_r n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor("0 114 178") lpattern(solid) lwidth(medthick) ///
        mcolor("0 114 178") msymbol(O) msize(small)) ///
    (connected run_r_mean n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor("213 94 0") lpattern(dash) lwidth(medthick) ///
        mcolor("213 94 0") msymbol(Dh) msize(small)) ///
    (line nis_r n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor(gs7) lpattern(dot) lwidth(medthin)), ///
    xlabel(0 30 100 200 300, ///
        nogrid labsize(small) labcolor(gs5) tlength(1) tlcolor(gs8)) ///
    ylabel($main_r_turn_min($main_r_turn_step)$main_r_turn_max, ///
        nogrid angle(horizontal) labsize(small) labcolor(gs5) ///
        format(%4.2f) tlength(1) tlcolor(gs8)) ///
    xscale(range($main_x_min $main_x_max) noextend lcolor(gs8) lwidth(thin)) ///
    yscale(range($main_r_turn_min $main_r_turn_max) noextend lcolor(gs8) lwidth(thin)) ///
    xtitle("Number of labelled examples", size(small) color(gs3) margin(small)) ///
    ytitle("Correlation (turnover intentions)", size(small) color(gs3) margin(small)) ///
    legend(order(2 "From averaged scores" 3 "Mean across iterations" ///
        1 "Individual iteration" 4 "NIS") ///
        position(6) ring(4) cols(2) size(small) symxsize(7) ///
        region(lstyle(none))) ///
    scheme($main_graph_scheme) ///
    graphregion(color(white) lcolor(white) margin(medium)) ///
    plotregion(color(white) lcolor(none)) ///
    xsize(8) ysize(5.5) name(main_turnover_correlation, replace)
graph export "$main_output/Figure_A3_turnover_correlation.png", replace width(2600)
graph export "$main_output/Figure_A3_turnover_correlation.pdf", replace
graph save "$main_output/Figure_A3_turnover_correlation.gph", replace

* 2. Turnover intentions: fully controlled standardized betas.
twoway ///
    (scatter beta plot_x if outcome == "Turnover intentions" & plot_summary == 0, ///
        mcolor(gs10) msymbol(Oh) msize(vsmall)) ///
    (connected primary_beta n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor("0 114 178") lpattern(solid) lwidth(medthick) ///
        mcolor("0 114 178") msymbol(O) msize(small)) ///
    (connected run_beta_mean n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor("213 94 0") lpattern(dash) lwidth(medthick) ///
        mcolor("213 94 0") msymbol(Dh) msize(small)) ///
    (line nis_beta n_examples if outcome == "Turnover intentions" & plot_summary == 1, ///
        sort lcolor(gs7) lpattern(dot) lwidth(medthin)), ///
    xlabel(0 30 100 200 300, ///
        nogrid labsize(small) labcolor(gs5) tlength(1) tlcolor(gs8)) ///
    ylabel($main_beta_turn_min($main_beta_turn_step)$main_beta_turn_max, ///
        nogrid angle(horizontal) labsize(small) labcolor(gs5) ///
        format(%4.2f) tlength(1) tlcolor(gs8)) ///
    xscale(range($main_x_min $main_x_max) noextend lcolor(gs8) lwidth(thin)) ///
    yscale(range($main_beta_turn_min $main_beta_turn_max) noextend lcolor(gs8) lwidth(thin)) ///
    xtitle("Number of labelled examples", size(small) color(gs3) margin(small)) ///
    ytitle("Standardized beta (turnover intentions)", size(small) color(gs3) margin(small)) ///
    legend(order(2 "From averaged scores" 3 "Mean across iterations" ///
        1 "Individual iteration" 4 "NIS") ///
        position(6) ring(4) cols(2) size(small) symxsize(7) ///
        region(lstyle(none))) ///
    scheme($main_graph_scheme) ///
    graphregion(color(white) lcolor(white) margin(medium)) ///
    plotregion(color(white) lcolor(none)) ///
    xsize(8) ysize(5.5) name(main_turnover, replace)
graph export "$main_output/Figure_A4_turnover_beta.png", replace width(2600)
graph export "$main_output/Figure_A4_turnover_beta.pdf", replace
graph save "$main_output/Figure_A4_turnover_beta.gph", replace


use "$data/Prolific_clean.dta", clear
