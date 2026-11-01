* Optional numerical comparison after do master.do prolific, default settings only.
* The reference was independently calculated from the original supplied study.
* This file is included only in the saved-score replication package.
if $main_runs_0 != 20 | $main_runs_30 != 10 | $main_runs_100 != 10 | $main_runs_200 != 10 | $main_runs_300 != 10 {
    display as error "Reference comparison requires the original 20/10/10/10/10 iterations."
    exit 198
}
use "$main_data/main_primary_estimates.dta", clear
tempfile current
keep n_examples outcome N correlation beta se p
save `current'
import delimited "$root/1. Analysis/tests/reference_primary.csv", clear varnames(1) case(preserve)
rename r reference_r
rename beta reference_beta
rename se reference_se
rename p reference_p
keep n_examples outcome reference_r reference_beta reference_se reference_p
merge 1:1 n_examples outcome using `current', assert(3) nogen
assert N == 446
assert abs(correlation - reference_r) < 0.000001
assert abs(beta - reference_beta) < 0.000001
assert abs(se - reference_se) < 0.000001
assert abs(p - reference_p) < 0.000001
display as result "All twelve primary regressions match the independent reference within 1e-6."
use "$data/Prolific_clean.dta", clear
