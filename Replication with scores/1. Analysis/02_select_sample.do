* Paths/settings come from master.do. All exclusions remain explicit in Stata.
*=============================================================================*
* Apply the survey sample restrictions, retaining an audit of all
*     original response records. Run 01_prepare_surveys.py in Spyder first.
*=============================================================================*
* The original Wave 1 CSV contains 1,050 response records plus two Qualtrics
* metadata records. Python retains all 1,050 response records in its output.
* This do-file shows every restriction that reduces that file to 446 people.
* The raw CSV files remain unchanged. An audit DTA retains all excluded rows.
* No rule here filters on an LLM prediction, regression result or p-value.
* No approval-list filter is used. The old speed_flag (<180 seconds) is unused.
*=============================================================================*

capture log close sample_selection
log using "$logs/02_select_sample.log", name(sample_selection) replace text

use "$data/Prolific_all_respondents.dta", clear
isid response_id
isid raw_w1_record
scalar bundled_raw_sources = bundled_sources[1]
assert bundled_sources == bundled_raw_sources
if bundled_raw_sources == 1 assert _N == 1050
count
display as result "All Wave 1 response records before exclusions: " %9.0fc r(N)

*=============================================================================*
* 1. Each criterion is visible separately, including its missing-value rule.
*=============================================================================*
generate byte pass_consent = consent_yes == 1
generate byte pass_current = is_current_yes == 1
generate byte pass_finished = finished == 1
generate byte pass_progress = progress == 100
generate byte pass_answers = mandatory_answers_present == 1
generate byte pass_pros = !missing(pros_wc) & pros_wc >= 25
generate byte pass_cons = !missing(cons_wc) & cons_wc >= 25
generate byte pass_pid = pid_available == 1 & participant_id != ""
generate byte pass_wave2 = paired_wave2 == 1
generate byte pass_duration = !missing(duration_sec) & duration_sec >= 150 & duration_sec < 900
generate byte pass_firm = firm_match_strict == 1
egen byte human_oi_items_observed = rownonmiss(w2_oi_1 w2_oi_2 w2_oi_3 w2_oi_4 w2_oi_5 w2_oi_6)
generate byte pass_human_oi = human_oi_items_observed == 6
foreach item of varlist w2_oi_1-w2_oi_6 {
    assert inrange(`item', 1, 7) & `item' == floor(`item') if !missing(`item')
}

label variable pass_consent "Consented to Wave 1"
label variable pass_current "Reported current employment at Wave 1"
label variable pass_finished "Wave 1 Finished flag equals 1"
label variable pass_progress "Wave 1 progress equals 100 percent"
label variable pass_answers "All original mandatory Wave 1 questions answered"
label variable pass_pros "Wave 1 Pros contains at least 25 words"
label variable pass_cons "Wave 1 Cons contains at least 25 words"
label variable pass_pid "Valid participant ID available for linkage"
label variable pass_wave2 "Participant ID links to a Wave 2 response"
label variable pass_duration "Wave 1 duration at least 150 and below 900 seconds"
label variable pass_firm "Strict normalised employer-name match across waves"
label variable pass_human_oi "All six Wave 2 OI items observed"

* The required-answer flag uses the exact original question list:
* Pros, Cons, seniority, tenure, firm size, age, gender, ethnicity, the six
* component ratings, CEO approval, recommendation, outlook and overall rating.
* Advice is optional. See Preparation_audit.json for original CSV column names.
* Firm matching uses entered Wave 2 Q24, never the embedded Wave 1 FirmName.
* No additional Wave 2 employee, completion, progress or duration filter is used.

*=============================================================================*
* 2. Retain ALL records and the first exclusion reason in a separate audit DTA.
*=============================================================================*
generate byte selected_for_analysis = pass_consent & pass_current & pass_finished & ///
    pass_progress & pass_answers & pass_pros & pass_cons & pass_pid & ///
    pass_wave2 & pass_duration & pass_firm & pass_human_oi
generate str100 first_exclusion_reason = ""
replace first_exclusion_reason = "Did not consent" if first_exclusion_reason == "" & !pass_consent
replace first_exclusion_reason = "Not a current employee at Wave 1" if first_exclusion_reason == "" & !pass_current
replace first_exclusion_reason = "Wave 1 not marked finished" if first_exclusion_reason == "" & !pass_finished
replace first_exclusion_reason = "Wave 1 progress below 100 percent or missing" if first_exclusion_reason == "" & !pass_progress
replace first_exclusion_reason = "Mandatory Wave 1 answer absent" if first_exclusion_reason == "" & !pass_answers
replace first_exclusion_reason = "Pros below 25 words or missing" if first_exclusion_reason == "" & !pass_pros
replace first_exclusion_reason = "Cons below 25 words or missing" if first_exclusion_reason == "" & !pass_cons
replace first_exclusion_reason = "Missing or invalid participant ID" if first_exclusion_reason == "" & !pass_pid
replace first_exclusion_reason = "No matched Wave 2 response" if first_exclusion_reason == "" & !pass_wave2
replace first_exclusion_reason = "Wave 1 duration outside [150,900) seconds or missing" if first_exclusion_reason == "" & !pass_duration
replace first_exclusion_reason = "Employer names do not meet the strict match rule" if first_exclusion_reason == "" & !pass_firm
replace first_exclusion_reason = "Fewer than six observed Wave 2 OI items" if first_exclusion_reason == "" & !pass_human_oi
replace first_exclusion_reason = "Included" if selected_for_analysis == 1
label variable selected_for_analysis "Meets every explicit Stata analysis-sample criterion"
assert first_exclusion_reason != ""
assert selected_for_analysis == (first_exclusion_reason == "Included")
save "$data/Prolific_exclusion_audit.dta", replace
tabulate first_exclusion_reason, missing

*=============================================================================*
* 3. Apply each restriction and export a step-by-step attrition table.
*=============================================================================*
capture postclose sample_flow
postfile sample_flow byte step str100 criterion long remaining removed ///
    using "$appendix/Supplement_A1_sample_attrition.dta", replace
scalar previous_count = _N
post sample_flow (0) ("All Wave 1 response records; excludes only Qualtrics metadata") (_N) (0)

keep if pass_consent == 1
post sample_flow (1) ("Consented to Wave 1") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After consent: " %9.0fc _N

keep if pass_current == 1
post sample_flow (2) ("Current employee at Wave 1") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After current-employee criterion: " %9.0fc _N

keep if pass_finished == 1
post sample_flow (3) ("Wave 1 marked finished") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Wave 1 finished flag: " %9.0fc _N

keep if pass_progress == 1
post sample_flow (4) ("Wave 1 progress equals 100 percent") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Wave 1 progress criterion: " %9.0fc _N

keep if pass_answers == 1
post sample_flow (5) ("All original mandatory Wave 1 answers present") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After mandatory-answer criterion: " %9.0fc _N

keep if pass_pros == 1
post sample_flow (6) ("Pros at least 25 words") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Pros word-count criterion: " %9.0fc _N

keep if pass_cons == 1
post sample_flow (7) ("Cons at least 25 words") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Cons word-count criterion: " %9.0fc _N

keep if pass_pid == 1
post sample_flow (8) ("Valid participant ID available") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After participant-ID criterion: " %9.0fc _N

keep if pass_wave2 == 1
post sample_flow (9) ("Matched to a Wave 2 response") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Wave 2 linkage: " %9.0fc _N

keep if pass_duration == 1
post sample_flow (10) ("Wave 1 duration: 150 <= seconds < 900") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After Wave 1 duration criterion: " %9.0fc _N

keep if pass_firm == 1
post sample_flow (11) ("Strict employer-name match between waves") (_N) (previous_count - _N)
scalar previous_count = _N
display as result "After strict employer-name match: " %9.0fc _N

keep if pass_human_oi == 1
post sample_flow (12) ("All six human OI items observed") (_N) (previous_count - _N)
postclose sample_flow
display as result "After complete human OI criterion: " %9.0fc _N

*=============================================================================*
* 4. Verify the selected sample; these assertions never choose participants.
*=============================================================================*
isid participant_id
assert selected_for_analysis == 1
if bundled_raw_sources == 1 assert _N == 446
* Fresh scoring begins from this selected sample. No earlier scores, scoring
* manifest or saved participant list is read or used to select respondents.
* The scoring script creates its new protocol only after this file is saved.
foreach var of varlist w2_oi_1-w2_oi_6 {
    assert !missing(`var')
}
assert !missing(w2_turnover_mean, nis_all_text, age, gender, ethnicity, ///
    overall_rating, rating_culture, ceo_approval, recommend, seniority, tenure_years)
sort participant_id
save "$data/Prolific_analysis.dta", replace
display as result "Saved the Stata-selected sample: 2. Data/Prolific_analysis.dta"
display as result "All Wave 1 records and exclusion reasons remain in Prolific_exclusion_audit.dta."

preserve
use "$appendix/Supplement_A1_sample_attrition.dta", clear
sort step
export delimited using "$appendix/Supplement_A1_sample_attrition.csv", replace
list step criterion remaining removed, noobs abbreviate(100)
restore
log close sample_selection
