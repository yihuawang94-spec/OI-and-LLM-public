"""Offline checks; optional full score verification after sample selection.
Run: python "1. Analysis/90_validate.py" [--scores]
No WRDS/Gemini calls; --scores exports/validates existing replies only.
"""
from pathlib import Path
import argparse,ast,hashlib,importlib.util,json,sys,tempfile,copy
ROOT=Path(__file__).resolve().parent.parent
A=ROOT/'1. Analysis'
sys.path.insert(0,str(A))
from project_settings import load_settings
from score_protocol import Requests,build_protocol,parse_reply
from oi_request_io import InvalidReply,ProtocolChanged,append_record,committed_records,digest

def module(name):
    spec=importlib.util.spec_from_file_location('check_'+name,A/(name+'.py'))
    result=importlib.util.module_from_spec(spec);sys.modules[spec.name]=result;spec.loader.exec_module(result);return result

def must_fail(fn,exception):
    try: fn()
    except exception: return
    raise AssertionError('Invalid input was unexpectedly accepted.')

def self_test():
    settings=load_settings(ROOT)
    for path in [ROOT/'master.py',*A.glob('*.py')]: ast.parse(path.read_text(encoding='utf-8-sig'))
    inventory=ROOT/'SOURCE_CHECKSUMS.json'
    if inventory.exists():
        for relative,expected in json.loads(inventory.read_text()).items():
            path=ROOT/relative
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise AssertionError('Bundled source changed: '+relative)
    reply={f'item{i}':{'score':i,'confidence':70,'no_evidence':False} for i in range(1,7)}
    assert parse_reply(json.dumps(reply))['item6']['score']==6
    bad=copy.deepcopy(reply);bad['item1']['score']=True
    must_fail(lambda:parse_reply(json.dumps(bad)),InvalidReply)
    bad=copy.deepcopy(reply);bad['item1']['no_evidence']=True
    must_fail(lambda:parse_reply(json.dumps(bad)),InvalidReply)
    bad=copy.deepcopy(reply);del bad['item4']
    must_fail(lambda:parse_reply(json.dumps(bad)),InvalidReply)
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'journal.jsonl'
        with path.open('w') as f: append_record(f,{'status':'ok'});f.write('{"unfinished":')
        before=path.read_bytes();assert list(committed_records(path))==[(1,{'status':'ok'})]
        assert path.read_bytes()==before
        assert list(committed_records(path,repair_tail=True))==[(1,{'status':'ok'})]
        assert path.read_bytes()==b'{"status": "ok"}\n'
        path.write_bytes(b'{"status": "ok"}\n{bad}\n')
        must_fail(lambda:list(committed_records(path,repair_tail=True)),RuntimeError)
    module('05_download_revelio').self_test()
    module('06_download_compustat').run_self_test()
    if (ROOT/'2. Data/Prolific_analysis.dta').exists():
        protocol=build_protocol(ROOT,settings);requests=Requests(protocol)
        changed=copy.deepcopy(settings);changed['iterations']['0']+=1;changed['scoring']['location']='global'
        assert digest(build_protocol(ROOT,changed))==requests.hash
        changed['scoring']['thinking_level']='HIGH'
        assert digest(build_protocol(ROOT,changed))!=requests.hash
        for f in protocol['folds']:
            for n in (30,100,200,300): assert set(f['test_ids']).isdisjoint(f['example_ids_by_count'][str(n)])
        assert len(list(requests.keys(settings['iterations'])))==446*sum(settings['iterations'].values())
    print('Offline validation passed. No live services or Stata regressions were executed.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scores',action='store_true');args=parser.parse_args()
    self_test()
    if args.scores: module('03_scores').run(ROOT,mode='export')
if __name__=='__main__': main()
