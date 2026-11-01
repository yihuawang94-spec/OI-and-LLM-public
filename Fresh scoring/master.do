* OI AND LLM -- one entry point for Stata.
* First run master.py in Spyder. In Stata, cd to this package and:
*   do master.do                  // all analyses
*   do master.do prolific         // survey analyses only
*   do master.do archival         // archival analyses only
*   do master.do sample           // sample selection only (also called by Python)
* An optional second argument supplies the package's absolute path.
version 16.0
clear all
set more off
set linesize 160
args stage project
if "`stage'" == "" local stage all
if !inlist("`stage'", "all", "sample", "prolific", "archival", "cost") {
    display as error "Stage must be all, sample, prolific, archival or cost."
    exit 198
}
if `"`project'"' == "" local project `"`c(pwd)'"'
cd `"`project'"'
confirm file "settings.json"
confirm file "2. Data/settings.do"
do "2. Data/settings.do"
global survey_correlates "$correlates"
* Use the current package path even if it was moved since Python last ran.
global root `"`c(pwd)'"'
global data "$root/2. Data"
global output "$root/3. Output/Main"
global main_output "$output"
global appendix "$root/3. Output/Appendix"
global logs "$root/3. Output/Logs"
global main_data "$data/Results"
global archival_data "$data"
if "$workforce_scope" == "GLOBAL" global archival_data "$data/global"
capture log close oi_master
* A batch invocation may already own master.log; use a distinct named log.
log using "$logs/workflow.log", name(oi_master) replace text
about

if "`stage'" == "sample" {
    capture erase "$data/sample_selection_complete.txt"
    do "$root/1. Analysis/02_select_sample.do"
    file open sample_done using "$data/sample_selection_complete.txt", write replace
    file write sample_done "Stata sample selection completed: " "`c(current_date)'" " " "`c(current_time)'" _n
    file close sample_done
}
if "`stage'" != "sample" {
    * Install only missing public Stata dependencies; versions are printed in the log.
    capture which esttab
    if _rc ssc install estout
    which esttab
    which estout
}
if inlist("`stage'", "all", "prolific") {
    do "$root/1. Analysis/04_build_analysis.do"
    do "$root/1. Analysis/10_main_results.do"
    do "$root/1. Analysis/11_turnover_benchmark.do"
    do "$root/1. Analysis/12_appendix_diagnostics.do"
}
if inlist("`stage'", "all", "archival") {
    capture which ftools
    if _rc ssc install ftools
    capture which reghdfe
    if _rc ssc install reghdfe
    which reghdfe
    which ftools
    do "$root/1. Analysis/20_turnover_actual.do"
    do "$root/1. Analysis/21_roa.do"
}
if inlist("`stage'", "all", "cost") {
    do "$root/1. Analysis/22_costs.do"
}
if inlist("`stage'", "all", "prolific") {
    global correlates "$survey_correlates"
    use "$data/Prolific_clean.dta", clear
}
display as result "Completed stage: `stage'. See 3. Output and OUTPUT_INDEX.csv."
log close oi_master
