"""Synthetic interruption/resume/cache test. No live API calls."""
from pathlib import Path
import sys,json,tempfile,copy,importlib.util
from types import SimpleNamespace as NS
A=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(A))
spec=importlib.util.spec_from_file_location('test_scoring',A/'03_scores.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
settings=json.loads((A.parent/'settings.json').read_text());settings['scoring']['google_cloud_project']='offline-fixture'
state={'calls':0,'prefixes':[],'configs':[],'clients':0}
text=json.dumps({f'item{i}':dict(score=4,confidence=60,no_evidence=False) for i in range(1,7)})
class Response:
 candidates=[NS(finish_reason='STOP')]
 def __init__(self): self.text=text
 def model_dump(self,**kwargs): return {'model_version':'fixture-version','usage_metadata':{'prompt_token_count':20,'candidates_token_count':15},'candidates':[{'finish_reason':'STOP'}]}
class Client:
 def __init__(self,**kwargs):
  state['clients']+=1;self.models=NS(generate_content=self.generate);self.caches=NS(create=self.create,delete=lambda **k:None)
 def create(self,**kwargs):
  config=kwargs['config'];state['prefixes'].append(config.contents[0].parts[0].text)
  return {'name':'fixture-cache-'+str(state['clients']),'usage_metadata':{'total_token_count':100}}
 def generate(self,**kwargs):
  state['calls']+=1;state['configs'].append(kwargs['config'])
  if state['calls']==2: raise RuntimeError('Simulated interruption after one accepted response.')
  return Response()
 def close(self): pass
class Requests:
 hash='fixture-protocol'
 people={'p':{}}
 protocol={'prompts':{'0':'system0','30':'system30'},'response_schema':{}}
 def keys(self,iterations): return iter([(30,1,'p'),(30,2,'p'),(0,1,'p')])
 def request(self,pid,n): return dict(participant_id=pid,n_examples=n,fold_id=1,text_hash='text',request_hash='request'+str(n),prefix='fixed-prefix' if n else '',suffix='target',cache_key='bank' if n else None)
types=NS(**{name:NS for name in ['HttpOptions','HttpRetryOptions','CreateCachedContentConfig','Content','Part','ThinkingConfig','AutomaticFunctionCallingConfig','GenerateContentConfig']})
m.import_google_sdk=lambda version:(NS(Client=Client),types)
original_version=m.importlib.metadata.version;m.importlib.metadata.version=lambda name:'fixture-sdk'
try:
 with tempfile.TemporaryDirectory() as folder:
  root=Path(folder);out=root/'2. Data/Scoring';out.mkdir(parents=True);(out/'protocol.json').write_text('{}')
  accepted={};attempts=[];req=Requests()
  try: m.collect(root,settings,req,accepted,attempts)
  except RuntimeError as e: assert 'Simulated interruption' in str(e)
  assert len(accepted)==1 and len(attempts)==2
  accepted,attempts=m.fresh_replies(root,req)
  m.collect(root,settings,req,accepted,attempts)
  assert len(accepted)==3 and len(attempts)==4 and state['calls']==4
  accepted2,attempts2=m.fresh_replies(root,req);assert accepted2.keys()==accepted.keys()
  m.collect(root,settings,req,accepted2,attempts2)
  assert state['calls']==4 and state['clients']==2
  assert state['prefixes']==['fixed-prefix','fixed-prefix']
  assert hasattr(state['configs'][0],'cached_content') and not hasattr(state['configs'][0],'system_instruction')
  assert state['configs'][-1].system_instruction=='system0' and not hasattr(state['configs'][-1],'cached_content')
  assert not list(out.glob('*.tmp'))
 print('PASS: crash/resume preserves accepted replies; caches rebuilt with identical content; completed requests never resent; zero-example request contains no bank.')
finally: m.importlib.metadata.version=original_version
