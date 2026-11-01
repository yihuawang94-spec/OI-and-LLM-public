"""Fixed prompts, held-out folds and banks. Iteration counts are scheduling settings."""
from pathlib import Path
import json
import math
import pandas as pd
from oi_request_io import digest, InvalidReply

COUNTS=(0,30,100,200,300)
TEXT={'Pros':'review_pros','Cons':'review_cons','Advice to management':'review_advice'}
HEADER='Review data follow as JSON:\n\n'

def parse_reply(text):
    try: obj=json.loads(text)
    except (TypeError,ValueError) as e: raise InvalidReply('Reply is not JSON.') from e
    if not isinstance(obj,dict) or set(obj)!={f'item{i}' for i in range(1,7)}:
        raise InvalidReply('Expected all six item keys.')
    result={}
    for i in range(1,7):
        x=obj[f'item{i}']
        if not isinstance(x,dict) or set(x)!={'score','confidence','no_evidence'}: raise InvalidReply('Invalid item fields.')
        a,b,c=x['score'],x['confidence'],x['no_evidence']
        if type(a)!=int or not 1<=a<=7 or type(b)!=int or not 0<=b<=100 or type(c)!=bool:
            raise InvalidReply('Invalid item response types or bounds.')
        if c and (a!=4 or b!=0): raise InvalidReply('No-evidence responses require score 4 and confidence 0.')
        result[f'item{i}']={'score':a,'confidence':b,'no_evidence':int(c)}
    return result

def build_protocol(root, settings):
    root=Path(root); study=root/'0. Raw/Study'
    sample=pd.read_stata(root/'2. Data/Prolific_analysis.dta',convert_categoricals=False)
    if len(sample)!=446 or sample.participant_id.duplicated().any(): raise ValueError('Expected 446 unique selected participants.')
    sample=sample.sort_values('participant_id').reset_index(drop=True)
    people=[]
    for row in sample.to_dict('records'):
        if row.get('selected_for_analysis',1)!=1: raise ValueError('Unselected respondent in input.')
        review={k:str(row[v]).strip() if pd.notna(row[v]) else '' for k,v in TEXT.items()}
        answers={f'item{i}':row[f'w2_oi_{i}'] for i in range(1,7)}
        if any(not math.isfinite(float(x)) or not 1<=x<=7 or x!=int(x) for x in answers.values()): raise ValueError('Incomplete human item answers.')
        answers={k:int(v) for k,v in answers.items()}
        people.append({'participant_id':row['participant_id'],'review':review,'human_answers':answers,'text_hash':digest(review)})
    frozen=json.loads((study/'folds_and_banks.json').read_text())
    folds=frozen['folds']; ids={r['participant_id'] for r in people}; seen=[]
    for f in folds:
        test=set(f['test_ids']);seen+=f['test_ids']
        if not test<=ids: raise ValueError('Fold contains an unknown participant.')
        largest=f['example_ids_by_count']['300']
        for n in COUNTS[1:]:
            bank=f['example_ids_by_count'][str(n)]
            if len(bank)!=n or len(set(bank))!=n or not set(bank)<=ids-test or bank!=largest[:n]:
                raise ValueError('Invalid bank: size, nesting, duplication or held-out overlap.')
    if len(seen)!=446 or set(seen)!=ids or len(set(seen))!=446: raise ValueError('Folds must partition the sample.')
    generation={k:settings['scoring'][k] for k in ('model','thinking_level','max_output_tokens')}
    return {'schema_version':1,'respondents':people,'folds':folds,'generation':generation,
            'prompts':{str(n):(study/('prompt_0.txt' if n==0 else 'prompt_examples.txt')).read_text(encoding='utf-8') for n in COUNTS},
            'response_schema':json.loads((study/'response_schema.json').read_text()),
            'sampling':'Provider defaults; no temperature, top_p, top_k or seed supplied.'}

class Requests:
    def __init__(self, protocol):
        self.protocol=protocol; self.hash=digest(protocol)
        self.people={x['participant_id']:x for x in protocol['respondents']}
        self.folds={pid:f for f in protocol['folds'] for pid in f['test_ids']}
        self.prefixes={}; self.hashes={}
        for f in protocol['folds']:
            for n in COUNTS[1:]:
                examples=[{'review':self.people[pid]['review'],'human_answers':self.people[pid]['human_answers']} for pid in f['example_ids_by_count'][str(n)]]
                self.prefixes[(f['fold_id'],n)]=HEADER+json.dumps({'labelled_examples':examples},ensure_ascii=False)[:-1]+', "review_to_score": '
    def request(self,pid,n):
        if n not in COUNTS or pid not in self.people: raise ValueError('Unknown condition or participant.')
        f=self.folds[pid]; row=self.people[pid]
        prefix=self.prefixes.get((f['fold_id'],n),'')
        suffix=json.dumps(row['review'],ensure_ascii=False)+'}' if n else HEADER+json.dumps({'review_to_score':row['review']},ensure_ascii=False)
        key=(pid,n)
        if key not in self.hashes:
            self.hashes[key]=digest({'system':self.protocol['prompts'][str(n)],'contents':prefix+suffix,'schema':self.protocol['response_schema']})
        return {'participant_id':pid,'n_examples':n,'fold_id':f['fold_id'],'text_hash':row['text_hash'],
                'request_hash':self.hashes[key],'prefix':prefix,'suffix':suffix,
                'cache_key':digest({'protocol':self.hash,'fold_id':f['fold_id'],'n_examples':n}) if n else None}
    def keys(self, iterations):
        # All iterations in a fold share one example cache; banks never change.
        for n in (30,100,200,300,0):
            if n==0:
                for run in range(1,iterations['0']+1):
                    for f in self.protocol['folds']:
                        for pid in f['test_ids']: yield (0,run,pid)
                continue
            for f in self.protocol['folds']:
                for run in range(1,iterations[str(n)]+1):
                    for pid in f['test_ids']: yield (n,run,pid)
