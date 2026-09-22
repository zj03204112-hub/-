import json, math, sqlite3
from collections import Counter, defaultdict
from compare_models import model_lambdas

DB='football_model_database.sqlite'
OUT='data/handicap_margin_optimization.json'
TARGET_LINES=(-3,-2,-1,1,2,3)
WEIGHTS=(0.0,0.15,0.25,0.35,0.50)
ALPHA=5.0


def pois(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

def tau(x,y,lh,la):
    rho=-0.05
    if x==0 and y==0:return 1-lh*la*rho
    if x==0 and y==1:return 1+lh*rho
    if x==1 and y==0:return 1+la*rho
    if x==1 and y==1:return 1-rho
    return 1.0

def line_probs(lh,la,line):
    p={'H':0.0,'D':0.0,'A':0.0}
    for i in range(10):
        for j in range(10):
            q=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            d=i+line-j
            p['H' if d>0 else 'D' if d==0 else 'A']+=q
    s=sum(p.values())
    return {k:v/s for k,v in p.items()}

def actual(fh,fa,line):
    d=fh+line-fa
    return 'H' if d>0 else 'D' if d==0 else 'A'

def margin_bin(x):
    if x<=-1.5:return 'LE_-1.5'
    if x<=-0.5:return 'M_-1.5_-0.5'
    if x<0.5:return 'M_-0.5_0.5'
    if x<1.5:return 'M_0.5_1.5'
    return 'GE_1.5'

def fit_meta(train):
    groups=defaultdict(Counter)
    for s in train:
        groups[(s['line'],margin_bin(s['expected_margin']))][s['actual']]+=1
    out={}
    for key,c in groups.items():
        n=sum(c.values())
        out[key]={k:(c[k]+ALPHA/3)/(n+ALPHA) for k in ('H','D','A')}
    return out

def blend(p,q,w):
    r={k:(1-w)*p[k]+w*q[k] for k in p}
    s=sum(r.values())
    return {k:v/s for k,v in r.items()}

def metric(): return {'n':0,'correct':0}
def add(m,pred,actual):
    m['n']+=1;m['correct']+=int(pred==actual)
def finish(m):
    return {'n':m['n'],'accuracy':round(m['correct']/m['n'],4) if m['n'] else None}

def main():
    con=sqlite3.connect(DB)
    rows=con.execute('''SELECT sm.match_id,sm.handicap,r.ft_home,r.ft_away,m.kickoff
      FROM sporttery_market sm JOIN results r ON r.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      WHERE sm.handicap IS NOT NULL AND sm.pool_code='asian_handicap_avg'
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL AND m.status='finished'
      ORDER BY m.kickoff,sm.match_id''').fetchall()
    chosen={}
    for mid,line,fh,fa,ko in rows:
        try:v=float(line); line=int(round(v)) if abs(v-round(v))<1e-9 else None
        except Exception: line=None
        if line not in TARGET_LINES: continue
        chosen.setdefault((mid,line),(mid,line,fh,fa,ko))
    samples=[]
    for mid,line,fh,fa,ko in chosen.values():
        match=con.execute('''SELECT match_id,kickoff,home_team,away_team,ft_home,ft_away
          FROM matches JOIN results USING(match_id) WHERE match_id=?''',(mid,)).fetchone()
        if not match: continue
        try:lh,la=model_lambdas(con,'v4',match)
        except Exception:continue
        samples.append({'mid':mid,'line':line,'kickoff':ko,'p':line_probs(lh,la,line),
                        'actual':actual(fh,fa,line),'expected_margin':lh-la})
    samples.sort(key=lambda x:(x['kickoff'],x['mid']))
    if len(samples)<60:
        json.dump({'status':'insufficient_samples','n':len(samples)},open(OUT,'w'),indent=2);return
    folds=[];n=len(samples);min_train=max(30,int(n*.4));remaining=n-min_train;chunk=max(10,remaining//4)
    for fi,start in enumerate(range(min_train,n,chunk)[:4],1):
        end=min(n,start+chunk);train=samples[:start];test=samples[start:end]
        meta=fit_meta(train)
        results={str(w):metric() for w in WEIGHTS}
        by_line={str(w):{str(line):metric() for line in (-1,1)} for w in WEIGHTS}
        for s in test:
            q=meta.get((s['line'],margin_bin(s['expected_margin'])))
            if q is None:q={'H':1/3,'D':1/3,'A':1/3}
            for w in WEIGHTS:
                p=blend(s['p'],q,w);pred=max(p,key=p.get)
                add(results[str(w)],pred,s['actual'])
                if s['line'] in (-1,1):add(by_line[str(w)][str(s['line'])],pred,s['actual'])
        folds.append({'fold':fi,'train_n':len(train),'test_n':len(test),'test_start':test[0]['kickoff'],'test_end':test[-1]['kickoff'],
                      'overall':{k:finish(v) for k,v in results.items()},
                      'pm1':{w:{l:finish(m) for l,m in d.items()} for w,d in by_line.items()}})
    aggregate={str(w):metric() for w in WEIGHTS}
    for f in folds:
        for w,v in f['overall'].items():
            aggregate[w]['n']+=v['n'];aggregate[w]['correct']+=round(v['accuracy']*v['n'])
    out={'status':'completed','model':'v4','sample_n':len(samples),'method':'training-only empirical outcome calibration conditioned on expected goal margin and handicap line','weights':WEIGHTS,
         'folds':folds,'aggregate':{w:finish(m) for w,m in aggregate.items()},
         'note':'Weights are reported for validation only; no test-slice weight selection and no future data leakage.'}
    json.dump(out,open(OUT,'w'),ensure_ascii=False,indent=2)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
