* USD token-cost scenarios from observed token counts and published Batch API prices.
* No API requests. No assumption that missing cached/thinking counts equal measured zero.
* The price inputs are scenarios, not an invoice reconciliation or a verified current quote.
use "$data/Scoring/scoring_attempts.dta", clear
keep if accepted == 1
foreach examples in 0 30 100 200 300 {
    local selected : copy global main_runs_`examples'
    drop if n_examples == `examples' & run > `selected'
}
isid participant_id n_examples run
assert !missing(prompt_tokens, output_tokens)
assert prompt_tokens >= 0 & output_tokens >= 0
gen byte thinking_reported = !missing(thinking_tokens)
gen byte caching_reported = !missing(cached_tokens)
gen double known_thinking = cond(missing(thinking_tokens), 0, thinking_tokens)
gen double known_cached = cond(missing(cached_tokens), 0, cached_tokens)
gen double observed_output = output_tokens + known_thinking
collapse (count) accepted=run (sum) prompt_total=prompt_tokens output_total=output_tokens ///
    thinking_total=known_thinking cached_total=known_cached ///
    n_thinking_reported=thinking_reported n_caching_reported=caching_reported ///
    (mean) prompt_mean=prompt_tokens output_mean=observed_output elapsed_mean=elapsed_seconds, by(n_examples)
gen double thinking_reporting_pct = 100*n_thinking_reported/accepted
gen double caching_reporting_pct = 100*n_caching_reported/accepted
gen double reported_cache_share_pct = 100*cached_total/prompt_total
label var output_mean "Mean output plus reported thinking; lower bound where thinking usage absent"
label var reported_cache_share_pct "Reported cached tokens / all prompt tokens; missing counts contribute zero only to this lower bound"
export delimited using "$appendix/Supplement_A7_observed_tokens.csv", replace
save "$main_data/observed_tokens.dta", replace
quietly summarize prompt_mean if n_examples == 0, meanonly
scalar cost_input_mean = r(mean)
quietly summarize output_mean if n_examples == 0, meanonly
scalar cost_output_mean = r(mean)
quietly summarize thinking_reporting_pct if n_examples == 0, meanonly
scalar cost_thinking_coverage = r(mean)

* Full archival coverage and the annual >=20 analysis subset, before missing DV/control exclusions.
use rcid year n_reviews n_nis_reviews using "$archival_data/analysis_nis_turnover.dta", clear
isid rcid year
assert !missing(rcid, year, n_reviews, n_nis_reviews)
assert n_reviews >= n_nis_reviews & n_nis_reviews >= 0
assert n_reviews == floor(n_reviews) & n_nis_reviews == floor(n_nis_reviews)
gen byte keep_all = 1
gen byte keep_annual = n_nis_reviews >= $min_reviews
capture postclose coverage
postfile coverage str12 sample double firms firm_years reviews using "$main_data/scoring_coverage.dta", replace
foreach sample in all annual {
    preserve
    keep if keep_`sample'
    scalar n_firmyears = _N
    egen byte firm_tag = tag(rcid)
    quietly count if firm_tag
    scalar n_firms = r(N)
    quietly summarize n_nis_reviews, meanonly
    post coverage ("`sample'") (n_firms) (n_firmyears) (r(sum))
    restore
}
postclose coverage
use "$main_data/scoring_coverage.dta", clear
gen int iterations = $cost_scoring_iterations
gen double requests = reviews * iterations
gen double input_tokens_mean = cost_input_mean
gen double output_tokens_mean = cost_output_mean
gen double thinking_reporting_pct = cost_thinking_coverage
foreach price in low high {
    gen double usd_`price' = requests * (input_tokens_mean * ${cost_input_usd_per_million_`price'} + ///
        output_tokens_mean * ${cost_output_usd_per_million_`price'}) / 1000000
    gen double budget_`price' = usd_`price' * (1+$cost_overhead_fraction)
}
label var usd_low "GPT-6 Luna Batch API scenario (low price fields)"
label var usd_high "Gemini 3.8 Flash Batch API scenario (high price fields)"
format firms firm_years reviews requests %15.0fc
format usd_* budget_* %15.2fc
save "$main_data/scoring_cost_scenarios.dta", replace
export delimited using "$appendix/Table_A7_cost.csv", replace
mkmat firms firm_years reviews usd_low budget_low usd_high budget_high, matrix(C) rownames(sample)
esttab matrix(C, fmt(0 0 0 2 2 2 2)) using "$appendix/Table_A7_cost.rtf", replace ///
    collabels("Firms" "Firm-years" "Reviews" "Luna USD" "Luna budget" "Gemini USD" "Gemini budget") ///
    title("Table A7. Scoring coverage and token-cost scenarios") nomtitles nonumbers nogaps ///
    note("all: full eligible review extract; annual: firm-years with at least $min_reviews usable reviews." ///
         "$cost_scoring_iterations iteration(s). Token quantities use accepted OILLM_0 survey calls; archival review lengths are unmeasured." ///
         "Low price fields: GPT-6 Luna Batch API rates. High price fields: Gemini 3.8 Flash Batch API rates. Rates are set in settings.json." ///
         "Token costs exclude failed attempts, cache storage, tax and staff; missing thinking counts can understate output cost." ///
         "Budgets add a $cost_overhead_fraction proportional allowance to the token charge.") ///
    substitute("\fs24" "\fs20")
list sample firms firm_years reviews usd_low budget_low usd_high budget_high, noobs
