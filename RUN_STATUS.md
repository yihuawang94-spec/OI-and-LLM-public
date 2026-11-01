# Run status

Status recorded on 1 November 2026. The supplied outputs come from a complete run on the user's computer on 1 November 2026, finishing at 11:59 UTC. No live WRDS, Gemini or Stata run took place in this repository session.

## Complete run, 1 November 2026

| Stage | Evidence and status |
| --- | --- |
| Survey preparation and selection | Completed. Stata sample selection finished with 446 respondents. |
| Survey analysis | Completed. Main Figures A1–A4, Appendix Tables A1–A5 and the supporting stability, error and benchmark outputs were generated. |
| WRDS retrieval | Completed. The Revelio manifest records completion at 11:56 UTC, reusing all 20 cached queries after the WRDS update probe matched the cache. The Compustat manifest records completion at 11:58 UTC. |
| Actual turnover | Completed. Table A6.1 uses up to 13,189 firm-years from 1,784 firms. |
| Return on assets | Completed. Table A6.2 uses up to 10,729 firm-years from 1,432 firms. |
| Scoring cost scenario | Completed. Table A7 prices one scoring pass with GPT-6 Luna and Gemini 3.8 Flash at published Batch API rates. For the full extract of 6,672,707 reviews, the token charge is $800.41 with GPT-6 Luna and $6,003.08 with Gemini 3.8 Flash, or $880.45 and $6,603.39 with the 10% allowance. |

Apart from Table A7, every table and figure matches the complete run of 31 October 2026, up to file timestamps and floating-point differences at the fifteenth significant digit.

The 31 October cost run stopped with `r(111)` because `22_costs.do` built the price global names without braces. Both packages now use `${cost_input_usd_per_million_`price'}` and `${cost_output_usd_per_million_`price'}`. The figures were regenerated without in-graph titles, with the outcome named on each y-axis.

## Supplied outputs

[Replication with scores/Supplied outputs/3. Output](<Replication with scores/Supplied outputs/3. Output>) holds the outputs of the complete run. Execution logs are excluded because they contain licence and organization details. The Compustat link and record audit files are excluded because they are derived from licensed data. The aggregate Revelio and Compustat coverage files and run manifests are included.

## De-identification, 1 November 2026

The raw files in this public repository are de-identified as described in [README.md](README.md#raw-files-and-de-identification). The supplied outputs come from the run on the files before de-identification. The following checks were run on the de-identified files:

| Check | Result |
| --- | --- |
| Changed values | Only Prolific participant IDs (replaced by codes), study and session IDs, IP addresses and location coordinates differ from the originals. Folds, banks, manifests and replies are otherwise identical. |
| Identifier search | No original participant, study or session ID, IP address or coordinate value remains in any file. |
| Archive restore | `restore_raw.py` rebuilds both response journals from the new archive and verifies their hashes. |
| Offline validation | `1. Analysis/90_validate.py` passes in both packages. |
| Survey preparation | `python master.py --stage prepare` reads all 1,050 Wave 1 and 740 Wave 2 IDs. Both prepared datasets match the original run in every variable apart from the participant codes, and sorting by participant ID gives the same record order. |
| Saved replies | All 26,760 accepted replies pass the package's verification against the de-identified scoring inputs. |

The Stata stages have yet to be rerun on the de-identified files.

## Preservation checks

The lossless archive preserves both de-identified response journals and all 26,760 saved predictions. `master.py` restores them and verifies their hashes, and `1. Analysis/90_validate.py` checks the files listed in `SOURCE_CHECKSUMS.json`.

## Earlier validation record

Each package's inherited `VALIDATION.txt` records checks from the earlier package build. Read it as that build's historical record.

## Company-mapping retrieval correction, 31 October 2026

Both copies of `05_download_revelio.py` reuse complete company-mapping checkpoints before live metadata inspection. New indexed mapping queries use 1,000-firm batches with individual checkpoints. If `EXPLAIN` shows a sequential scan of the mapping table, remaining firms are queried together to avoid repeated full scans. TCP keepalive settings help detect dead connections where supported, and login, setup and query progress are reported separately. Offline regression checks cover interruption and resume, existing complete caches, empty or unmapped firms, code formatting, freshness and the sequential-scan fallback. This correction does not change the per-firm NAICS definitions.
