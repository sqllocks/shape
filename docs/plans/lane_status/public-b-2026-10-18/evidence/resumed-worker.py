import datetime,fcntl,hashlib,json,os,pathlib,shutil,subprocess,sys,time
base=pathlib.Path('/workspace/public-b-validation');control=base/'control.json';now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
def z():return sum(s.startswith('Z') for s in subprocess.check_output(['ps','-eo','stat'],text=True).splitlines())
def save(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def git(cwd,*args):return subprocess.check_output(['git',*args],cwd=cwd,text=True).strip()
def snap(cwd):
 diff=subprocess.check_output(['git','diff','HEAD','--','.',':(exclude)docs/plans/lane_status/**'],cwd=cwd)
 raw_untracked=git(cwd,'ls-files','--others','--exclude-standard').splitlines()
 untracked='\n'.join(x for x in raw_untracked if not x.startswith('docs/plans/lane_status/') and not (x=='not-json.tmp' and (cwd/x).is_file() and (cwd/x).stat().st_size==0))
 return {'head':git(cwd,'rev-parse','HEAD'),'tree':git(cwd,'rev-parse','HEAD^{tree}'),'status':git(cwd,'status','--porcelain'),'source_diff':hashlib.sha256(diff).hexdigest(),'untracked_source':untracked,'zombies':z()}
uv='uv run --locked --group docs --group dbt --group benchmark '
while True:
 cfg=json.loads(control.read_text()) if control.exists() else {'pause':True}
 if cfg.get('stop'):break
 if cfg.get('pause') or not cfg.get('lane'):time.sleep(5);continue
 lane=cfg['lane'];head=cfg['head'];cwd=pathlib.Path('/workspace/shape-'+lane.lower());root=base/'runs'/(lane+'-'+head[:12]);root.mkdir(parents=True,exist_ok=True)
 statepath=root/'state.json';state=json.loads(statepath.read_text()) if statepath.exists() else {'lane':lane,'expected_head':head,'jobs':{},'created':now()}
 jobs=[('bootstrap','make bootstrap UV=/workspace/.onboarding/tools/bin/uv'),('collection-proof','python /workspace/public-b-validation/collection-proof.py '+str(root/'collection')),
 ('make-check','make check UV=/workspace/.onboarding/tools/bin/uv'),('native-extra-heavy',uv+'pytest -q -m "heavy and not emulator and not live" tests/joint'),('native-content',uv+'pytest -q -m "not emulator and not live" tests/demo/content'),
 ('native-fabric','UV_PROJECT_ENVIRONMENT='+str(cwd/'.venv-fabric')+' uv run --locked --group fabric-demo --group dbt pytest -q -m "not emulator and not live" tests/demo/fabric'),
 ('python-core','SHAPE_KERNEL=python '+uv+'pytest -q -m "not emulator and not live" --ignore=tests/demo/fabric'),
 ('python-fabric','SHAPE_KERNEL=python UV_PROJECT_ENVIRONMENT='+str(cwd/'.venv-fabric')+' uv run --locked --group fabric-demo --group dbt pytest -q -m "not emulator and not live" tests/demo/fabric')]
 if state.get('stage')=='complete':save(base/'status.json',{'stage':'awaiting-next-lane','lane':lane,'head':head,'state':str(statepath)});time.sleep(5);continue
 failed=False;boundary_pause=False
 for name,command in jobs:
  fresh=json.loads(control.read_text())
  if fresh.get('stop') or fresh.get('pause') or fresh.get('lane')!=lane or fresh.get('head')!=head:
   save(base/'status.json',{'stage':'paused-at-boundary','lane':lane,'next_job':name,'state':str(statepath)});boundary_pause=True;break
  prior=state['jobs'].get(name,{})
  if prior.get('exit')==0 and prior.get('valid') is True:continue
  if prior:
   stamp=now().replace(':','').replace('.','');archive=root/'attempts'/(name+'-'+stamp);archive.mkdir(parents=True,exist_ok=True)
   for old in (root/(name+'.log'),root/(name+'-cache.log')):
    if old.exists():shutil.move(str(old),str(archive/old.name))
   if name=='collection-proof' and (root/'collection').exists():shutil.move(str(root/'collection'),str(archive/'collection'))
   save(archive/'entry.json',prior);state.setdefault('archived_attempts',[]).append({'job':name,'directory':str(archive),'entry':prior});save(statepath,state)
  with (base/'full-check.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);before=snap(cwd)
   if before['head']!=head or before['source_diff']!=hashlib.sha256(b'').hexdigest() or before['untracked_source']:
    save(base/'status.json',{'stage':'source-changed','lane':lane,'before':before});failed=True;break
   if before['zombies']>1000:save(base/'status.json',{'stage':'hard-stop-zombies','before':before});sys.exit(1)
   entry={'command':command,'started':now(),'before':before,'stage':'running','log':str(root/(name+'.log'))};state['jobs'][name]=entry;state['stage']='running';save(statepath,state);save(base/'status.json',{'stage':'running','lane':lane,'head':head,'job':name,'state':str(statepath)})
   # All prior jobs have finished; only rebuildable cache for this own lane is removed.
   target=cwd/'rust/shape-kernel/target'
   if target.exists():shutil.rmtree(target)
   cleanenv={k:v for k,v in os.environ.items() if k not in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')}
   with (root/(name+'-cache.log')).open('w') as log:clean=subprocess.run(['docker','--host=unix:///var/run/docker.sock','builder','prune','--force'],env=cleanenv,stdout=log,stderr=subprocess.STDOUT)
   entry['cache_cleanup_exit']=clean.returncode
   shell='set -e; . /workspace/public-b-validation/test-env.sh; export SHAPE_KERNEL=rust; '+command
   with pathlib.Path(entry['log']).open('w') as log:
    log.write('Command: '+command+'\nStorage: pytest tmp_path_retention_policy=failed; task helper sourced after lock.\n');log.flush()
    r=subprocess.run([sys.executable,os.path.expanduser('~/bin/reap.py'),'bash','-c',shell],cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
   after=snap(cwd);entry.update(exit=r.returncode,finished=now(),after=after,stage='completed',valid=(r.returncode==0 and before['head']==after['head'] and before['source_diff']==after['source_diff'] and before['untracked_source']==after['untracked_source']))
   save(statepath,state)
   if after['zombies']>1000:save(base/'status.json',{'stage':'hard-stop-zombies','after':after});sys.exit(1)
   if not entry['valid']:state['stage']='failed';save(statepath,state);save(base/'status.json',{'stage':'failed','lane':lane,'job':name,'state':str(statepath)});failed=True;break
 if boundary_pause:time.sleep(5);continue
 if failed:
  cfg['pause']=True;save(control,cfg);time.sleep(5);continue
 state.update(stage='complete',finished=now());save(statepath,state);save(base/'status.json',{'stage':'awaiting-next-lane','lane':lane,'head':head,'state':str(statepath)})
