OI AND LLM -- TWO SELF-CONTAINED PACKAGES

Replication with scores
  Preserves the original 26,760 LLM predictions and verifies their saved replies.
  Rebuilds the survey sample and downloads Revelio/Compustat from WRDS.
  Does not contact Gemini.

Fresh scoring
  Contains the same de-identified surveys, prompts, fixed folds/banks and analysis code.
  Includes no original LLM predictions. Generates 20 zero-example iterations and
  10 each at 30/100/200/300 examples, using frozen banks and explicit caching.
  Downloads Revelio/Compustat from WRDS independently. New scoring incurs charges.

In your chosen package, read README.txt, edit settings.json, run master.py in
Spyder, and then run master.do in Stata. The original project is not needed.
Keep the packages separate. No WRDS extracts or credentials are bundled.
The public survey exports retain all 1,050 Wave 1 and 740 Wave 2 response records;
Stata shows the exclusions to 446. Both firm-match indicators remain available.
Participant IDs were replaced with codes; study/session IDs, IP addresses and
location coordinates were removed. README.md describes the de-identification.
For survey-only replication without WRDS, see WITHOUT WRDS ACCESS in the
package's README.txt.

The four main figures and all appendix regression tables are named in
OUTPUT_INDEX.csv. Supplied outputs are in
"Replication with scores/Supplied outputs/3. Output".

Preserved: sample, prompts, fold/bank membership and order, item scoring, accepted
responses, current FE specifications and annual >=20 review rule.
Retired: old importer, OI3 branch, optional 100-run branch, obsolete single-iteration
appendix, Wald/GMM branches and redundant scoring entry points.

The supplied outputs come from a complete run on 1 November 2026, before
de-identification: survey analyses for 446 respondents, archival turnover and
return-on-assets tables, and both model cost scenarios. On the public de-identified
files, the prepared survey datasets match the original run apart from participant
codes, and all 26,760 saved replies pass verification. The Stata stages have yet to be
rerun on these public files. See RUN_STATUS.md for the current record; each
package's VALIDATION.txt describes earlier checks.

Cost reporting uses the Batch API prices recorded on 1 November 2026 in
settings.json: GPT-6 Luna at US$0.05 per million input tokens and US$0.25 per
million output tokens; Gemini 3.8 Flash at US$0.375 and US$1.875, respectively.
The default is one scoring iteration, with a 10% budget allowance. For ten
iterations, set cost.scoring_iterations to 10, run python master.py --stage settings,
then run do master.do cost in Stata after preparing the survey and archival data.
These are illustrative scenarios based on Gemini token usage. Luna requires
validation before full-scale scoring. Sources and Gemini's scheduled January 2027
price increase are recorded in settings.json.
