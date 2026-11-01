# OI and LLM

Two independent packages reproduce or rerun the survey scoring and rebuild the archival data used in this project. Keep the packages in separate folders.

This repository includes the de-identified raw survey exports, saved scoring evidence and lossless response archive, alongside the code and supplied outputs. See [Raw files and de-identification](#raw-files-and-de-identification) for what was removed.

| Package | Scoring |
| --- | --- |
| [Replication with scores](<Replication with scores/README.txt>) | Reuses all 26,760 original predictions and verifies the saved replies. Makes no Gemini requests. |
| [Fresh scoring](<Fresh scoring/README.txt>) | Starts with no saved predictions and makes new Gemini requests. New scoring incurs charges. |

Both packages download and process Revelio and Compustat through WRDS. Licensed WRDS access is required; WRDS extracts and credentials are not bundled.

## Run a package

Use Python 3.10+ and Stata 16+. From the chosen package folder, install the data dependencies into the Python environment you will run:

```sh
python -m pip install -r requirements.txt
```

For Fresh scoring, also install:

```sh
python -m pip install -r requirements-scoring.txt
```

Edit that package's `settings.json`: set the Stata executable if needed and use your WRDS authentication. Fresh scoring also needs Google Cloud Application Default Credentials and your Google Cloud project. Keep passwords and credential files outside the repository.

Run `master.py` in Spyder, or run:

```sh
python master.py
```

After Python finishes successfully, open Stata in the same package folder and run:

```stata
do master.do
```

Each package's `README.txt` gives settings, separate stages, dependencies and output names. `OUTPUT_INDEX.csv` maps the tables and figures to their generating scripts.

## Raw files and de-identification

To protect participants' anonymity, identifiers were removed from the raw files before public release:

- Each Prolific participant ID was replaced with a 24-character code, `a00000000000000000000001` to `a00000000000000000001051`, assigned in the sorted order of the original IDs. The same codes appear in the survey exports, the frozen folds and example banks, the scoring manifests and the saved response journals. Three participants had entered their Prolific ID as their employer's name, and those entries carry the same codes.
- Prolific study and session IDs, IP addresses and location coordinates were emptied in both survey exports.

All other contents are as recorded, including every survey answer and every saved LLM reply. The scoring manifests' protocol hashes, the response archive and the stored checksums were recomputed for the de-identified files. Because the codes keep the original sort order, the analysis code runs unchanged.

The two large response journals are stored in a lossless archive split into 25 small parts. The saved-score package's `master.py` restores the journals automatically and verifies their hashes. See [RAW_ARCHIVE.txt](<Replication with scores/RAW_ARCHIVE.txt>) for details and the standalone restore command. The fresh package remains independent and contains no saved predictions.

## Results and verification

The supplied outputs under [Replication with scores/Supplied outputs/3. Output](<Replication with scores/Supplied outputs/3. Output>) come from a complete run on 1 November 2026: survey results for 446 respondents, the archival turnover and return-on-assets tables, and the scoring cost scenario. Execution logs remain out of the repository because they contain licence and organization details. The Compustat link and record audit files are also excluded because they are derived from licensed data. These outputs were produced from the files before de-identification. On the de-identified files, the Python survey preparation gives the same data apart from the participant codes, and all 26,760 saved replies pass the package's verification. See [RUN_STATUS.md](RUN_STATUS.md) for the run record.

The proposal and manuscript are outside this repository's scope.
