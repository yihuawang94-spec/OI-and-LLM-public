"""Open in Spyder and run. settings.json is the only editable configuration.

Default: prepare surveys, run the Stata selection, obtain/export scores, and
re-download the archival data from WRDS. Then run master.do for regressions.
CLI: python master.py --stage prepare|sample|scores|wrds|settings|all
An export-only scoring pass is available with --stage scores --export-only.
"""
from pathlib import Path
import argparse,importlib.util,json,os,shutil,subprocess,sys,time

ROOT=Path(__file__).resolve().parent
ANALYSIS=ROOT/'1. Analysis'
if str(ANALYSIS) not in sys.path: sys.path.insert(0,str(ANALYSIS))
from project_settings import load_settings,write_stata_settings

def module(name):
    spec=importlib.util.spec_from_file_location('oi_'+name,ANALYSIS/(name+'.py'))
    obj=importlib.util.module_from_spec(spec);sys.modules[spec.name]=obj;spec.loader.exec_module(obj)
    return obj

def stata_binary(settings):
    configured=settings['stata_executable']
    if configured:
        found=Path(configured).expanduser()
        if not found.is_file(): raise FileNotFoundError('stata_executable in settings.json is not a file.')
        return str(found)
    for name in ('stata-mp','stata-se','stata','StataMP-64','StataSE-64','StataMP','StataSE'):
        candidate=shutil.which(name)
        if candidate: return candidate
    if os.name=='nt':
        for base in (Path(os.environ.get('ProgramFiles','C:/Program Files')),Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))):
            for folder in sorted(base.glob('Stata*'),reverse=True):
                for name in ('StataMP-64.exe','StataSE-64.exe','StataMP.exe','StataSE.exe','StataBE-64.exe','StataBE.exe','Stata-64.exe','Stata.exe'):
                    if (folder/name).is_file(): return str(folder/name)
    elif sys.platform=='darwin':
        for name in ('StataMP','StataSE','StataBE','Stata'):
            for path in Path('/Applications').glob(f'Stata*/{name}.app/Contents/MacOS/stata*'):
                if path.is_file(): return str(path)
    raise FileNotFoundError('Set stata_executable in settings.json to your Stata program. Alternatively, after --stage prepare, open Stata in this package folder, run do master.do sample, then use --stage scores and --stage wrds in Python.')

def select_sample(settings):
    executable=stata_binary(settings)
    marker=ROOT/'2. Data/sample_selection_complete.txt'
    marker.unlink(missing_ok=True)
    # /e (Windows) and -e (macOS) exit without a completion prompt.
    batch='/e' if os.name=='nt' else ('-e' if sys.platform=='darwin' else '-b')
    args=[executable,batch,'do',str(ROOT/'master.do'),'sample',str(ROOT)]
    print('Stata is applying the sample criteria; see 3. Output/Logs/02_select_sample.log.',flush=True)
    process=subprocess.Popen(args,cwd=ROOT/'3. Output/Logs')
    started=time.monotonic()
    while True:
        try: code=process.wait(timeout=30);break
        except subprocess.TimeoutExpired: print(f'Stata sample selection still running after {time.monotonic()-started:.0f}s.',flush=True)
    if code or not marker.exists(): raise RuntimeError('Stata did not finish sample selection. Read 3. Output/Logs/master.log and 02_select_sample.log; no scoring was started.')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['all','prepare','sample','scores','wrds','revelio','compustat','settings'],default='all')
    parser.add_argument('--export-only',action='store_true')
    args=parser.parse_args(argv)
    # Restore missing saved response logs and check their original bytes.
    restore_spec=importlib.util.spec_from_file_location('oi_restore_raw',ROOT/'restore_raw.py')
    restore_module=importlib.util.module_from_spec(restore_spec)
    restore_spec.loader.exec_module(restore_module)
    restore_module.restore_raw(ROOT)
    settings=load_settings(ROOT);write_stata_settings(ROOT,settings)
    print(f"Package: {settings['package_mode']}; settings: {ROOT/'settings.json'}",flush=True)
    if args.stage=='settings': print('Stata settings refreshed.');return
    if args.stage in ('all','prepare'): module('01_prepare_surveys').prepare(ROOT)
    if args.stage in ('all','sample'): select_sample(settings)
    if args.stage in ('all','scores'):
        result=module('03_scores').run(ROOT,mode='export' if args.export_only else 'score')
        if not result.status_complete.all():
            raise RuntimeError('Some selected scores remain incomplete. Accepted replies are saved. Rerun to resume, or lower iteration counts in settings.json for a preliminary analysis.')
    extra=['--refresh'] if settings['wrds']['refresh'] else []
    if args.stage in ('all','wrds','revelio'): module('05_download_revelio').main(extra)
    if args.stage in ('all','wrds','compustat'): module('06_download_compustat').main(extra)
    next_step={
        'prepare':'Next: run do master.do sample in Stata, or run master.py --stage sample.',
        'sample':'Next: run master.py --stage scores and master.py --stage wrds.',
        'scores':'Scores ready. Run do master.do prolific in Stata; use --stage wrds to prepare the archival analyses.',
        'revelio':'Next: run master.py --stage compustat.',
        'compustat':'Next: run do master.do archival in Stata.',
        'wrds':'Archival data ready. Run do master.do archival in Stata.',
    }.get(args.stage,'Data preparation finished. Run master.do in Stata for all analyses and numbered outputs.')
    print(next_step,flush=True)

if __name__=='__main__': main()
