OI AND LLM -- TWO SELF-CONTAINED PACKAGES

Replication with scores
  Preserves the original 26,760 LLM predictions and verifies their raw replies.
  Rebuilds the survey sample and downloads Revelio/Compustat from WRDS.
  Does not contact Gemini.

Fresh scoring
  Contains the same raw surveys, prompts, fixed folds/banks and analysis code.
  Includes no original LLM predictions. Generates 20 zero-example iterations and
  10 each at 30/100/200/300 examples, using frozen banks and explicit caching.
  Downloads Revelio/Compustat from WRDS independently.

In your chosen package, read README.txt, edit settings.json, run master.py in
Spyder, and then run master.do in Stata. The original project is not needed.
Keep the packages separate. No WRDS extracts or credentials are bundled.
The survey raw exports retain ALL 1,050 Wave 1 and 740 Wave 2 response records;
Stata shows the exclusions to 446. Both firm-match indicators remain available.

The four main figures and all appendix regression tables are named in
OUTPUT_INDEX.csv. The proposal itself has not been edited.

Preserved: sample, prompts, fold/bank membership and order, item scoring, accepted
responses, current FE specifications and annual >=20 review rule.
Retired: old importer, OI3 branch, optional 100-run branch, obsolete single-iteration
appendix, Wald/GMM branches and redundant scoring entry points.

Validated: exact score preservation, raw-to-analysis variable agreement, independent
numeric checks and offline WRDS/cache/resume tests. Stata/live-service execution
remains to be checked on your machine. See VALIDATION.txt.

Cost reporting retains the supplied upper-price assumption and leaves the lower
price blank pending a documented quote; settings.json makes both editable.
