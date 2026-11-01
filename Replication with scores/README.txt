OI AND LLM
Richard Wang

THIS PACKAGE: REPLICATION WITH ORIGINAL SAVED SCORES. No Gemini calls.

START HERE

1. Extract this package to its own folder. Keep the two packages separate.
2. Open settings.json. Set your Stata executable if it is not found automatically,
   and your WRDS username if your existing WRDS authentication does not supply it.
   Only the fresh-scoring package needs a Google Cloud project and credentials.
3. Open master.py in Spyder and run it. Python prepares the survey files, asks
   Stata to apply the documented exclusions, obtains the LLM scores, and downloads
   Revelio and Compustat data from WRDS. The first archival download is required
   in BOTH packages. An interrupted download can resume its own checkpoints.
   Without a WRDS account, follow WITHOUT WRDS ACCESS below instead.
4. In Stata, change directory to this package and run:
       do master.do
   The regressions remain in Stata. Tables are RTF files that Word opens directly;
   the four figures are saved as PDF, PNG and editable Stata graphs.

WITHOUT WRDS ACCESS

The survey analyses run without WRDS. In Python:
    python master.py --stage prepare
    python master.py --stage sample
    python master.py --stage scores
Then in Stata, from the package folder:
    do master.do prolific
This rebuilds Tables A1-A5, Figures A1-A4 and the survey supplements.
Tables A6.1, A6.2 and A7 use Revelio and Compustat data licensed through
WRDS, so they need a WRDS account. Results from the author's run are in
"Replication with scores/Supplied outputs".

The package mode is printed when Python starts. Replication verifies and reuses
saved responses, without contacting Gemini. Fresh scoring submits new requests.
The default fresh plan is 26,760 accepted requests: 446 participants x 60 scoring
iterations. Retries can add calls. Fresh results are not expected to be identical
to the original responses.

If Stata cannot be found, put its full program path in settings.json, for example
C:/Program Files/Stata19/StataMP-64.exe (use your actual executable filename).
Alternatively, run Python with --stage prepare, run do master.do sample in Stata,
and then run Python's scores and wrds stages separately. Do not type the commands
inside a do-file one line at a time when they contain /// continuations.

DEPENDENCIES

Python 3.10+ with pandas and numpy. WRDS downloading additionally needs wrds and
its dependencies. Fresh scoring uses google-genai; the study used version 2.22.0.
The requirements files separate data preparation from the optional scoring SDK.
Install packages into the Python environment used by Spyder. Use your existing
WRDS authentication and Google Cloud Application Default Credentials; credentials
are not included in this package or written to settings.json.

Stata 16+ is required. master.do installs estout, ftools and reghdfe only if missing,
and prints their installed locations/versions in the log. The figures use standard
Stata graph options; plotplain is used if already installed, otherwise s2color.

ONE SETTINGS FILE

Edit settings.json, then rerun master.py, or just:
    python master.py --stage settings
before running Stata again. The generated 2. Data/settings.do should not be edited.

iterations: number of consecutive iterations included for each example count.
Defaults are 20 for 0 examples and 10 each for 30, 100, 200 and 300 examples.
Lower counts use the first selected iterations; higher counts preserve previous
responses and add missing iterations in the fresh package. The replication package
contains only the original 20/10/10/10/10 and will report missing scores if more
are requested. No condition is silently dropped. Incomplete selected iterations
stop the Stata analysis rather than changing the participant sample.

scoring: model, endpoint, timeout, retry and cache settings. Changing the endpoint
or iteration counts does not invalidate accepted responses. Changing the model,
reasoning configuration, prompts, review texts, human labels or example banks
requires a separate fresh package. This prevents incompatible replies being mixed.

analysis: demographic/correlate globals, archival control lists, annual review
cutoff and ROA winsorization percentiles. Default archival selection is at least
20 usable reviews IN EACH INCLUDED FIRM-YEAR. The full extract is not prefiltered.

 graphs: each entry gives [minimum, maximum, interval] for the y-axis. The x-axis
contains exactly 0, 30, 100, 200 and 300. Charts stop with an explanation if edited
limits would omit a plotted value. Turnover charts have zero at the upper end.

wrds: explicit review years (2008-2026), workforce scope, Compustat coverage and
refresh switch. The defaults preserve the US-workforce/North-American-accounts
specification in the supplied work. English-language reviews by current employees
are selected. Enabling Global Compustat requires the corresponding subscription
and changes coverage. GLOBAL workforce outputs use 2. Data/global. Set refresh to
true for a deliberate complete new download, then set it back to false to resume.
Provider revisions can change archival counts and estimates on a later download;
the retrieval manifests and query checkpoints record what was obtained.

cost: number of prospective scoring iterations, USD token prices and budget
allowance. Costs use observed OILLM_0 tokens, including reported thinking, with
unknown usage explicitly identified. The low price fields hold GPT-6 Luna Batch
API rates and the high price fields hold Gemini 3.8 Flash Batch API rates, both
from the providers' official pricing pages accessed 1 November 2026. Missing
prices are never silently replaced with zero. The throughput fields are kept for
settings validation and are not used. Actual archival review lengths have not
been measured.

RESUMING REVELIO DOWNLOADS

Keep the same package folder and its 0. Raw/Revelio checkpoints. Stop the old
running process before replacing 1. Analysis/05_download_revelio.py, then rerun
with wrds.refresh=false. Existing recent-year freshness checks still apply.
Do not use --refresh simply to resume an interrupted download.

Workforce queries use 100-firm batches. Company/NAICS mappings now use separate
1,000-firm checkpoints when the WRDS query plan supports indexed batching.
If that plan scans the whole mapping table for each batch, the script queries
all remaining firms once to avoid repeating full scans. Existing complete
mapping checkpoints are reused before any live mapping-table inspection.
The per-firm modal NAICS rule and all analysis definitions are unchanged.

"Connecting to WRDS" includes login, Duo and connection setup; "query started"
means the named SQL request has begun. The script sets network connection and
server statement timeouts, and TCP keepalive options where supported. These
settings do not impose a timer on human login or Duo responses. Keep any login
prompt visible when using Spyder.

FILES AND RUN ORDER

master.py                 Python preparation/scoring/WRDS entry point.
master.do                 Stata selection or analysis entry point.
settings.json             All user-facing settings.
OUTPUT_INDEX.csv          Numbered outputs, their purpose and generating file.
SOURCE_CHECKSUMS.json     Checksums of bundled raw inputs and scoring evidence.
VALIDATION.txt            Checks completed and execution limits.

0. Raw                    Original surveys, fixed study prompts/folds/banks;
                          replication also contains original response journals.
                          WRDS checkpoints are created here on your first pull.
1. Analysis               Active Python and Stata code; helpers are imported by
                          the numbered scripts. No old project-folder importer.
2. Data                   Rebuilt survey, scoring and archival datasets; numerical
                          model summaries go under Results.
3. Output/Main            Figures A1-A4 for the main body.
3. Output/Appendix        Numbered regression/cost tables and supporting summaries.
3. Output/Logs            Preparation, scoring, WRDS and Stata execution logs.

01_prepare_surveys.py codes ALL 1,050 Wave 1 and ALL 740 Wave 2 responses. The raw
exports also contain two Qualtrics metadata rows per file. No response is deleted
from either raw CSV. Recruitment of 1,000 people is not the same as the 1,050
response records actually present in this export.

02_select_sample.do shows every restriction and retains all 1,050 Wave 1 records
in Prolific_exclusion_audit.dta with their first exclusion reason. It selects the
same 446 people as the supplied study. Both firm_match_strict and firm_match_lenient
are rebuilt and retained. Python does not use the saved scores to select people.

03_scores.py verifies archived replies or collects fresh replies. The prompt text,
response schema, five original folds and ordered nested example banks are preserved.
A participant's own labels never appear in the bank used to predict that participant.
Example banks stay fixed across iterations. Fresh example conditions use explicit
caching, including iteration 1; the original study's iteration 1 used full prompts,
with later iterations explicitly cached. The logical prompt contents are unchanged;
transport metadata records this difference. Caching is not model fine-tuning.

Accepted responses are appended durably. Restarting never replaces an accepted
response. It can recreate an expired Google cache with the SAME bank and order.
The provider's reported model version is checked to avoid silently mixing model
revisions. In replication, original manifests retain their historical settings;
those historical scheduling fields do not determine the active plan. The only
active iteration settings are in settings.json. No NumPy-version match is required.

04_build_analysis.do creates the analysis variables before any regression and saves:
    Prolific_clean.dta        one row per participant; all variables together;
    Prolific_iterations.dta   one row per participant/condition/iteration.
Useful wide-file names:
    oi, turnover, NIS                  human scores and dictionary measure;
    z_oi, z_turnover, z_nis             standardized versions;
    full_0 ... full_300                raw averaged LLM scores;
    z_full_0 ... z_full_300             standardized averaged scores;
    full_100_r1, z_full_100_r1, etc.     individual iterations;
    gender ethnicity age               demographics;
    overall_rating rating_culture ceo_approval recommend seniority tenure_years
                                       six correlates, plus their z_ counterparts.

For standalone regressions, first run master.do prolific, or load settings.do and
Prolific_clean.dta. For example:
    regress z_turnover z_oi z_full_200 $demographics $z_correlates, vce(robust)
The globals persist after the master file finishes. The substantive models in
10_main_results.do and 11_turnover_benchmark.do are written explicitly.

10_main_results.do makes Tables A1-A4 and Figures A1-A4. Primary models standardize
AFTER averaging each participant's scores. Separate iteration models standardize
each iteration separately. The solid line is the coefficient/correlation using
averaged scores; the dashed line is the mean of separately estimated results;
points show every selected iteration. NIS is a fixed horizontal reference.

11_turnover_benchmark.do makes the STANDALONE Table A5 (turnover on human OI plus
each proxy and controls) and a separate controls-only/human-OI benchmark. This is
not merged into the main turnover table. No causal mediation claim is imposed.

12_appendix_diagnostics.do reports original-scale MAE, RMSE and signed error,
participant score dispersion, and the mean/SD/range of correlations and fully
controlled betas across iterations. P-values are reported individually or as
ranges, never averaged. Across-iteration SD is not a population confidence interval.
Pearson correlation p-values use the conventional correlation test; regression
p-values use robust SEs. These can differ even in an unadjusted one-proxy model.

20_turnover_actual.do and 21_roa.do retain the supplied fixed-effects regressions,
year/industry/firm effects and firm-clustered SEs. The default controlled turnover
model includes current-year turnover; the ROA model includes current-year ROA.
Both outcomes refer to the following year. ROA cutoffs are calculated from current
ROA before filtering and applied to BOTH current and lead ROA, matching the source
33 file. Model-specific missing-data exclusions remain. No A-B/GMM is included.
The prepared archival variables are also saved as Turnover_clean.dta and ROA_clean.dta.

22_costs.do counts reviews in the FULL extract and in the annual review-count subset,
then projects token costs from the selected OILLM_0 usage and the price fields in settings.json.

RUNNING ONLY PART OF THE WORK

Spyder Run > Configuration per file > Command line options, or a terminal:
    python master.py --stage prepare
    python master.py --stage sample
    python master.py --stage scores
    python master.py --stage scores --export-only
    python master.py --stage wrds
    python master.py --stage revelio
    python master.py --stage compustat
    python master.py --stage settings

Stata, from the package folder:
    do master.do prolific
    do master.do archival
    do master.do cost
These stages use the same settings. Existing raw surveys and accepted LLM replies
are never overwritten. Derived datasets and tables are rebuilt in this package.

VERIFICATION

Optional offline checks:
    python "1. Analysis/90_validate.py"
After Stata sample selection, also verify/re-export the saved replies:
    python "1. Analysis/90_validate.py" --scores
Neither command makes Gemini or WRDS requests. Synthetic cache tests are available
at 1. Analysis/tests/scoring_resume.py and also make no live requests.

The proposal DOCX is not rewritten or bundled here. OUTPUT_INDEX.csv preserves
Tables A1-A4 and Figures A1-A4 from its current main text, retains archival Table
A6.1/A6.2 numbering, uses Table A5 for the joint turnover models and Table A7 for
costs, and identifies additional supporting files. Old Wald/GMM appendix tables
and old single-iteration result tables are not generated. Appendix prose and its
main-text cross-references can be finalized from this output map separately.
