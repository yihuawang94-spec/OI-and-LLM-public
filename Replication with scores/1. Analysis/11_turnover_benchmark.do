* Standalone benchmark and joint models; separate from main turnover tables.
use "$data/Prolific_clean.dta", clear
isid participant_id
assert _N == $comparison_N
fvset base 1 gender ethnicity

regress z_turnover $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_controls

regress z_turnover z_oi $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_human

regress z_turnover z_oi z_nis $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_nis

regress z_turnover z_oi z_full_0 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_0

regress z_turnover z_oi z_full_30 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_30

regress z_turnover z_oi z_full_100 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_100

regress z_turnover z_oi z_full_200 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_200

regress z_turnover z_oi z_full_300 $demographics $z_correlates, vce(robust)
assert e(N) == $comparison_N
estimates store tb_j_300
esttab tb_j_nis tb_j_0 tb_j_30 tb_j_100 tb_j_200 tb_j_300 ///
    using "$appendix/Table_A5_turnover_controlling_human_OI.rtf", replace ///
    rename(z_nis proxy z_full_0 proxy z_full_30 proxy z_full_100 proxy z_full_200 proxy z_full_300 proxy) keep(z_oi proxy) order(z_oi proxy) ///
    coeflabels(z_oi "Human OI" proxy "Text proxy") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("NIS" "OILLM_0" "OILLM_30" "OILLM_100" "OILLM_200" "OILLM_300") ///
    stats(N, labels("Participants") fmt(%9.0f)) nonumbers nogaps compress ///
    title("Table A5. Turnover intentions conditional on human OI and each proxy") ///
    note("Standardized variables; robust standard errors in parentheses; p-values below." ///
         "All models include demographics and the six Glassdoor correlates. LLM scores average selected iterations." ///
         "These conditional associations do not identify causal mediation or a pure OI component.") ///
    substitute("\fs24" "\fs20")

esttab tb_controls tb_human using "$appendix/Supplement_A5_human_OI_benchmark.rtf", replace ///
    keep(z_oi) coeflabels(z_oi "Human OI") ///
    cells("b(star fmt(3)) se(par fmt(3)) p(fmt(3))") star(* 0.05 ** 0.01 *** 0.001) ///
    mtitles("Controls only" "+ Human OI") stats(N, labels("Participants") fmt(%9.0f)) ///
    title("Turnover intentions: human OI benchmark") ///
    note("Both models include demographics and six Glassdoor correlates. Robust SEs; standardized outcome and human OI.") ///
    nonumbers nogaps compress ///
    substitute("\fs24" "\fs20")
