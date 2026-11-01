"""Export archived replies or collect new six-item scores; no regressions here."""
from pathlib import Path
from datetime import datetime,timezone
import json,math,random,time,importlib.metadata
import pandas as pd
import numpy as np
from project_settings import load_settings
from score_protocol import build_protocol,Requests,parse_reply,COUNTS
from oi_request_io import (digest,read_json,atomic_text,atomic_dta,committed_records,
    append_record,record_model,run_lock,console_log,request_progress,import_google_sdk,
    error_code,ProtocolChanged,InvalidReply)
from oi_context_cache import ContextCacheManager

USAGE={'prompt_tokens':'prompt_token_count','output_tokens':'candidates_token_count',
       'thinking_tokens':'thoughts_token_count','cached_tokens':'cached_content_token_count','total_tokens':'total_token_count'}

def compact(record, n, source, source_protocol, default_endpoint):
    return {k:record.get(k) for k in ('participant_id','run','fold_id','text_hash','request_hash','status','prediction','attempt','utc','elapsed_seconds','error_type','error_code')} | {
        'n_examples':n,'model_version':record_model(record) or '',
        'source_log':source,'source_protocol_hash':source_protocol,
        'endpoint_location':record.get('endpoint_location',default_endpoint),
        'transport_layout':record.get('transport_layout') or 'legacy_full_json_v1',
        **{name:(record.get('usage') or {}).get(field,np.nan) for name,field in USAGE.items()}}

def accept_record(record, req, completed, n, source, source_protocol, default_endpoint, version):
    for field in ('participant_id','fold_id','text_hash','request_hash'):
        if record.get(field)!=req[field]: raise ProtocolChanged(f'Saved {field} does not match the review and prompt in {source}.')
    if type(record.get('run'))!=int or record['run']<1: raise ProtocolChanged('Invalid iteration number.')
    row=compact(record,n,source,source_protocol,default_endpoint)
    if row['status']=='ok':
        if parse_reply(record.get('response_text'))!=record.get('prediction'): raise ProtocolChanged('Saved item scores differ from the reply.')
        if not row['model_version'] or row['model_version']!=version: raise ProtocolChanged('Reported model version differs within the study.')
        key=(n,row['run'],row['participant_id'])
        if key in completed: raise ProtocolChanged('Duplicate accepted response.')
        completed[key]=row
    elif row['status']!='error': raise ProtocolChanged('Invalid response status.')
    return row

def archived_replies(root, requests):
    directory=Path(root)/'0. Raw/LLM_scores'; completed={}; attempts=[]
    version=read_json(directory/'fresh_main/model_version_lock.json')['reported_model_version']
    for name in ('fresh_main','fixed_examples'):
        folder=directory/name; manifest=read_json(folder/'manifest.json'); design=manifest['design']
        if digest(design)!=manifest['protocol_hash']: raise ProtocolChanged('Original manifest hash mismatch.')
        for field in ('model','thinking_level','max_output_tokens'):
            if design['settings'][field]!=requests.protocol['generation'][field]: raise ProtocolChanged(f'Saved scoring setting differs: {field}.')
        if design['respondents']!=requests.protocol['respondents']: raise ProtocolChanged('Selected reviews or human answers differ from the original scoring inputs.')
        for f in design['folds']:
            ref=next(x for x in requests.protocol['folds'] if x['fold_id']==f['fold_id'])
            if f['test_ids']!=ref['test_ids'] or f['example_ids_by_count']!=ref['example_ids_by_count']:
                raise ProtocolChanged('Original folds or example banks differ.')
        for line,record in committed_records(folder/'responses.jsonl'):
            if record.get('protocol_hash')!=manifest['protocol_hash']: raise ProtocolChanged(f'Original response protocol mismatch in {name}, line {line}.')
            arm=record['arm']; n=0 if arm=='zsfull' else int(arm.removeprefix('fsfull_'))
            req=requests.request(record['participant_id'],n)
            attempts.append(accept_record(record,req,completed,n,name,manifest['protocol_hash'],design['settings']['location'],version))
    return completed,attempts

def fresh_replies(root, requests, repair=False):
    directory=Path(root)/'2. Data/Scoring'; path=directory/'responses.jsonl'
    completed={};attempts=[];lock=directory/'model_version.json'
    version=read_json(lock)['reported_model_version'] if lock.exists() else None
    if path.exists() and not (directory/'protocol.json').exists(): raise ProtocolChanged('Saved responses lack their protocol.')
    for line,r in committed_records(path,repair_tail=repair):
        if r.get('protocol_hash')!=requests.hash: raise ProtocolChanged(f'Saved scoring inputs changed at line {line}.')
        req=requests.request(r['participant_id'],r['n_examples'])
        attempts.append(accept_record(r,req,completed,r['n_examples'],'fresh',requests.hash,r.get('endpoint_location',''),version))
    return completed,attempts

def export_scores(root,requests,iterations,completed,attempts):
    root=Path(root); out=root/'2. Data/Scoring';out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for n in COUNTS:
        for run in range(1,iterations[str(n)]+1):
            run_complete=all((n,run,pid) in completed for pid in requests.people)
            for pid in sorted(requests.people):
                row=completed.get((n,run,pid)); req=requests.request(pid,n)
                r={'participant_id':pid,'n_examples':n,'run':run,'fold_id':req['fold_id'],
                   'bank_set':int(n>0),'bank_repeat':run if n else 0,'status_complete':int(row is not None),
                   'run_complete':int(run_complete),'protocol_hash':requests.hash}
                for name in ('source_protocol_hash','model_version','transport_layout','endpoint_location'):
                    r[name]=(row or {}).get(name,'') or ''
                for i in range(1,7): r[f'oi_item{i}']=row['prediction'][f'item{i}']['score'] if row else np.nan
                r['oi_full']=float(np.mean([r[f'oi_item{i}'] for i in range(1,7)]))
                rows.append(r)
    scores=pd.DataFrame(rows)
    atomic_dta(scores,out/'scores_long.dta','OI full scale: each accepted iteration; missing requests remain missing')
    fields=['participant_id','n_examples','run','fold_id','attempt','utc','elapsed_seconds','error_type','error_code','model_version','source_log','source_protocol_hash','endpoint_location','transport_layout',*USAGE]
    usage=pd.DataFrame([{k:x.get(k) for k in fields}|{'accepted':int(x['status']=='ok')} for x in attempts],columns=fields+['accepted'])
    strings=['participant_id','utc','error_type','model_version','source_log','source_protocol_hash','endpoint_location','transport_layout']
    for key in usage:
        usage[key]=usage[key].fillna('').astype(str) if key in strings else pd.to_numeric(usage[key],errors='coerce').astype(float)
    atomic_dta(usage,out/'scoring_attempts.dta','Every recorded attempt; missing usage is unknown')
    status=scores.groupby(['n_examples','run'],as_index=False).agg(accepted=('status_complete','sum'),expected=('participant_id','size'))
    status.to_csv(out/'completion.csv',index=False)
    atomic_text(out/'status.json',json.dumps({'accepted':int(scores.status_complete.sum()),'planned':len(scores),'iterations':iterations,'complete':bool(scores.status_complete.all()),'protocol_hash':requests.hash},indent=2)+'\n')
    print(f"Saved {int(scores.status_complete.sum()):,}/{len(scores):,} selected six-item predictions.",flush=True)
    return scores

def retry_delay(attempt): return min(15*2**(attempt-1),55)+random.uniform(0,5)

def collect(root,settings,requests,completed,attempts):
    cfg=settings['scoring']; out=Path(root)/'2. Data/Scoring'
    pending=[k for k in requests.keys(settings['iterations']) if k not in completed]
    if not pending: return
    if not cfg['google_cloud_project']: raise ValueError('Set scoring.google_cloud_project in settings.json before fresh scoring.')
    genai,types=import_google_sdk(cfg['google_genai_version'])
    client=genai.Client(enterprise=True,project=cfg['google_cloud_project'],location=cfg['location'],
        http_options=types.HttpOptions(api_version=cfg['api_version'],timeout=cfg['timeout_seconds']*1000,retry_options=types.HttpRetryOptions(attempts=1)))
    manager=ContextCacheManager(client,types,cfg['model'],cfg['location'],out/'cache_events.jsonl',ttl_seconds=cfg['cache_ttl_seconds'],heartbeat_seconds=cfg['heartbeat_seconds'])
    version_path=out/'model_version.json'; version=read_json(version_path)['reported_model_version'] if version_path.exists() else None
    failures=0
    try:
        with (out/'responses.jsonl').open('a',encoding='utf-8',newline='\n') as stream:
            for index,(n,run,pid) in enumerate(pending,1):
                req=requests.request(pid,n)
                description=f'Request {index}/{len(pending)}; {n} examples; iteration {run}; fold {req["fold_id"]}'
                for attempt in range(1,cfg['max_attempts']+1):
                    row={k:req[k] for k in ('participant_id','n_examples','fold_id','text_hash','request_hash')}
                    row.update(run=run,attempt=attempt,protocol_hash=requests.hash,utc=datetime.now(timezone.utc).isoformat(),endpoint_location=cfg['location'],transport_layout='explicit_cache_prefix_v1' if n else 'full_json_v1',sdk_version=importlib.metadata.version('google-genai'))
                    started=time.monotonic()
                    try:
                        config={'response_mime_type':'application/json','response_schema':requests.protocol['response_schema'],
                            'thinking_config':types.ThinkingConfig(thinking_level=cfg['thinking_level'],include_thoughts=False),
                            'max_output_tokens':cfg['max_output_tokens'],'automatic_function_calling':types.AutomaticFunctionCallingConfig(disable=True)}
                        if n:
                            cache_name=manager.ensure(req['cache_key'],req['prefix'],requests.protocol['prompts'][str(n)])
                            config['cached_content']=cache_name;row.update(manager.metadata)
                        else:
                            manager.close();config['system_instruction']=requests.protocol['prompts']['0']
                        with request_progress(description,cfg['heartbeat_seconds']):
                            response=client.models.generate_content(model=cfg['model'],contents=req['suffix'],config=types.GenerateContentConfig(**config))
                        raw=response.model_dump(mode='json',exclude_none=True)
                        row.update(raw_response=raw,response_text=response.text,usage=raw.get('usage_metadata') or {},model_version=raw.get('model_version'))
                        reported=record_model(row)
                        if not reported or (version is not None and version!=reported): raise ProtocolChanged('Reported model version changed or is absent.')
                        candidates=response.candidates or []
                        if len(candidates)!=1 or str(getattr(candidates[0].finish_reason,'value',candidates[0].finish_reason))!='STOP': raise InvalidReply('Incomplete or unexpected candidate response.')
                        row['prediction']=parse_reply(response.text)
                        if version is None:
                            version=reported;atomic_text(version_path,json.dumps({'reported_model_version':version,'protocol_hash':requests.hash},indent=2)+'\n')
                    except Exception as exc:
                        row.update(status='error',error_type=type(exc).__name__,error_code=error_code(exc),error=str(exc),elapsed_seconds=time.monotonic()-started)
                        append_record(stream,row);attempts.append(compact(row,n,'fresh',requests.hash,cfg['location']))
                        print(f'{description}: {type(exc).__name__}, HTTP {error_code(exc)}; attempt saved.',flush=True)
                        if isinstance(exc,ProtocolChanged): raise
                        code=error_code(exc)
                        if code==404 and n: manager.invalidate('generation_not_found',row.get('cache_name'))
                        retryable=code in {404,408,429,499,500,502,503,504} or isinstance(exc,(InvalidReply,TimeoutError,ConnectionError)) or type(exc).__name__ in {'ReadTimeout','ConnectTimeout','ConnectError','RemoteProtocolError','PoolTimeout'}
                        if not retryable: raise
                        if attempt<cfg['max_attempts']:
                            delay=retry_delay(attempt); print(f'Retrying in {delay:.1f}s.',flush=True);time.sleep(delay)
                        else:
                            failures+=1
                            if failures>=cfg['consecutive_error_limit']: raise RuntimeError('Stopped after consecutive failed reviews. Rerun to resume saved progress.')
                    else:
                        row.update(status='ok',elapsed_seconds=time.monotonic()-started)
                        append_record(stream,row)
                        short=compact(row,n,'fresh',requests.hash,cfg['location']);attempts.append(short);completed[(n,run,pid)]=short
                        failures=0;print(f'{description}: saved ({len(completed):,} accepted).',flush=True);break
    finally:
        manager.close();client.close()

def run(root,mode='score'):
    root=Path(root).resolve(); settings=load_settings(root); protocol=build_protocol(root,settings);requests=Requests(protocol)
    out=root/'2. Data/Scoring';out.mkdir(parents=True,exist_ok=True)
    if settings['package_mode']=='replication':
        completed,attempts=archived_replies(root,requests)
        return export_scores(root,requests,settings['iterations'],completed,attempts)
    with run_lock(out/'run.lock'):
        path=out/'protocol.json'
        if path.exists() and read_json(path)!=protocol: raise ProtocolChanged('Reviews, human answers, banks, prompts or generation settings changed. Use a separate fresh package for a new scoring protocol.')
        if not path.exists():
            if (out/'responses.jsonl').exists(): raise ProtocolChanged('Saved responses lack their protocol.')
            atomic_text(path,json.dumps(protocol,ensure_ascii=False,indent=2)+'\n')
        completed,attempts=fresh_replies(root,requests,repair=True)
        try:
            if mode=='score':
                with console_log(root/'3. Output/Logs/scoring.log'): collect(root,settings,requests,completed,attempts)
        finally:
            result=export_scores(root,requests,settings['iterations'],completed,attempts)
    return result

if __name__=='__main__': run(Path(__file__).resolve().parent.parent)
