import json,os,pathlib,subprocess,sys
out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
base=['/workspace/.onboarding/tools/bin/uv','run','--locked','--group','docs','--group','dbt','--group','benchmark','pytest','--collect-only','-q']
selections={
 'full-core':['-m','not emulator and not live','--ignore=tests/demo/fabric'],
 'make-light':['-m','not emulator and not live and not heavy','--ignore=tests/demo/fabric','--ignore=tests/demo/content'],
 'make-heavy':['-m','heavy','tests/kernel','tests/profile','tests/streaming'],
 'extra-heavy':['-m','heavy and not emulator and not live','tests/joint'],
 'content':['-m','not emulator and not live','tests/demo/content'],
}
sets={};results=[]
for name,args in selections.items():
 command=[sys.executable,os.path.expanduser('~/bin/reap.py'),*base,*args]
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
 results.append({'name':name,'command':command,'exit':r.returncode})
 if r.returncode:print(json.dumps(results));sys.exit(r.returncode)
 lines=(out/(name+'.log')).read_text().splitlines();ids=[l.strip() for l in lines if l.startswith('tests/') and '::' in l]
 assert ids and len(ids)==len(set(ids)),name+' invalid collection output'
 sets[name]=set(ids)
union=set().union(*(sets[k] for k in ('make-light','make-heavy','extra-heavy','content')))
report={'commands':results,'counts':{k:len(v) for k,v in sets.items()},'missing':sorted(sets['full-core']-union),'unexpected':sorted(union-sets['full-core']),'equal':union==sets['full-core'],'fabric':'separate Arrow19 full-directory runs in both modes'}
(out/'proof.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));sys.exit(0 if report['equal'] else 1)
