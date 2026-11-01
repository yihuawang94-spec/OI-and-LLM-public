"""One editable settings file for Python and Stata."""
import json
from pathlib import Path

COUNTS = (0, 30, 100, 200, 300)

def load_settings(root):
    s = json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8-sig'))
    if s['package_mode'] not in ('replication', 'fresh'):
        raise ValueError('package_mode must be replication or fresh.')
    if set(s['iterations']) != {str(x) for x in COUNTS}:
        raise ValueError('iterations must specify 0, 30, 100, 200 and 300 examples.')
    for value in s['iterations'].values():
        if type(value) is not int or value < 1:
            raise ValueError('Every iteration count must be a positive integer.')
    for key in ('start_year','end_year'):
        if type(s['wrds'][key]) is not int: raise ValueError('Set explicit WRDS study years.')
    if s['wrds']['start_year'] > s['wrds']['end_year']: raise ValueError('Invalid WRDS years.')
    if s['wrds']['workforce_scope'] not in ('US','GLOBAL'): raise ValueError('workforce_scope must be US or GLOBAL.')
    if s['analysis']['expected_n'] != 446: raise ValueError('This study has 446 selected participants.')
    for name, values in s['graphs'].items():
        if len(values)!=3 or values[0]>=values[1] or values[2]<=0: raise ValueError(f'Invalid graph limits: {name}')
    if type(s['analysis']['min_reviews_per_firm_year']) is not int or s['analysis']['min_reviews_per_firm_year']<1:
        raise ValueError('The annual review cutoff must be a positive integer.')
    lo,hi=s['analysis']['roa_winsor_percentiles']
    if not 0<lo<hi<100: raise ValueError('ROA percentiles must satisfy 0 < low < high < 100.')
    for key in ('timeout_seconds','max_attempts','heartbeat_seconds','cache_ttl_seconds','consecutive_error_limit','max_output_tokens'):
        if type(s['scoring'][key]) is not int or s['scoring'][key]<1: raise ValueError(f'Invalid scoring setting: {key}')
    if s['scoring']['cache_ttl_seconds']<60: raise ValueError('Cache TTL must be at least 60 seconds.')
    c=s['cost']
    if type(c['scoring_iterations']) is not int or c['scoring_iterations']<1: raise ValueError('Invalid cost iteration count.')
    if not 0<c['reviews_per_minute_low']<=c['reviews_per_minute_high'] or not 0<c['hours_per_day']<=24:
        raise ValueError('Invalid throughput assumptions.')
    for key,value in c.items():
        if 'usd_per_million' in key and value is not None and (not isinstance(value,(float,int)) or value<0):
            raise ValueError('Token prices must be nonnegative numbers or null.')
    if c['overhead_fraction']<0: raise ValueError('Budget allowance must be nonnegative.')
    return s

def write_stata_settings(root, settings=None):
    root=Path(root).resolve(); s=settings or load_settings(root)
    d={'root':root.as_posix(), 'data':(root/'2. Data').as_posix(),
       'output':(root/'3. Output/Main').as_posix(), 'appendix':(root/'3. Output/Appendix').as_posix(),
       'logs':(root/'3. Output/Logs').as_posix(), 'main_data':(root/'2. Data/Results').as_posix(),
       'main_output':(root/'3. Output/Main').as_posix(), 'comparison_N':s['analysis']['expected_n'],
       'expected_N':s['analysis']['expected_n'], 'demographics':s['analysis']['demographics'],
       'correlates':s['analysis']['correlates'],
       'z_correlates':' '.join('z_'+x for x in s['analysis']['correlates'].split()),
       'min_reviews':s['analysis']['min_reviews_per_firm_year'],
       'turnover_controls':s['analysis']['turnover_controls'], 'roa_controls':s['analysis']['roa_controls'],
       'roa_p_low':s['analysis']['roa_winsor_percentiles'][0], 'roa_p_high':s['analysis']['roa_winsor_percentiles'][1]}
    for n,r in s['iterations'].items(): d['main_runs_'+n]=r
    d['workforce_scope']=s['wrds']['workforce_scope']
    d['archival_data']=(root/'2. Data'/('global' if s['wrds']['workforce_scope']=='GLOBAL' else '')).as_posix()
    for name,values in s['graphs'].items():
        for suffix,val in zip(('min','max','step'),values): d['graph_'+name+'_'+suffix]=val
    for key,val in s['cost'].items():
        if key!='note': d['cost_'+key]='.' if val is None else val
    lines=['* Generated from settings.json by master.py. Edit settings.json, then rerun master.py with --stage settings.']
    for key,val in d.items():
        if isinstance(val,str):
            if any(x in val for x in ('"','\n','\r','`')): raise ValueError(f'Unsupported quote/newline in setting {key}.')
            lines.append(f'global {key} "{val}"')
        else: lines.append(f'global {key} {val}')
    for rel in ('2. Data/Results','3. Output/Main','3. Output/Appendix','3. Output/Logs'): (root/rel).mkdir(parents=True,exist_ok=True)
    path=root/'2. Data/settings.do'; path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return path
