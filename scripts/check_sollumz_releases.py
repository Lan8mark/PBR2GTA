"""Download official releases and run isolated Blender compatibility checks.

Requires gh, this project's Python environment, two Blender executables, the
full release ZIP and a separately installed NVTT. No user profile is modified.
Example:
  python scripts/check_sollumz_releases.py --prepare --run
Use --versions 2.8.0,2.9.0 for a subset. New published stable releases >= 2.7
are included automatically; their results are never assumed from version numbers.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

REPO=Path(__file__).resolve().parents[1]
ROOT=REPO/'build/compat-matrix'


def api(path):
    return json.loads(subprocess.check_output(['gh','api',path]))


def dependencies(source):
    path=source/'dependencies.py'
    if not path.exists():return []
    tree=ast.parse(path.read_text(encoding='utf-8'))
    declaration=next(n.value for n in tree.body if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=='DEPENDENCIES' for t in n.targets))
    result=[]
    for item in declaration.elts:
        name=ast.literal_eval(item.args[0])
        version=next(arg.value for arg in item.args if isinstance(arg,ast.Constant)
                     and isinstance(arg.value,str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[a-zA-Z0-9.]+)?',arg.value))
        result.append(f'{name}=={version}')
    return result


def prepare(release):
    version=release['tag_name'].removeprefix('v');folder=ROOT/version;folder.mkdir(exist_ok=True)
    asset=next(a for a in release['assets'] if a['name']=='Sollumz.zip')
    archive=folder/'Sollumz.zip'
    if not archive.exists():urllib.request.urlretrieve(asset['browser_download_url'],archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if asset.get('digest'):assert asset['digest']=='sha256:'+digest
    destination=folder/'source'
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            target=(destination/item.filename).resolve()
            if not target.is_relative_to(destination.resolve()):raise ValueError('Unsafe release ZIP path')
        z.extractall(destination)
    source=next(p.parent for p in destination.glob('*/blender_manifest.toml'))
    required=dependencies(source)
    if required:
        key=hashlib.sha256('|'.join(required).encode()).hexdigest()[:12]
        deps=ROOT/('deps-'+key)
        if not (deps/'ready.json').exists():
            subprocess.run([sys.executable,'-m','pip','install','--target',str(deps),'--python-version','3.11',
                            '--only-binary=:all:',*required,'--extra-index-url','https://static.cfx.re/whl/'],check=True)
            (deps/'ready.json').write_text(json.dumps(required))
        (folder/'deps_path.txt').write_text(str(deps))
    return {'version':version,'url':asset['browser_download_url'],'sha256':digest,'dependencies':required}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--versions')
    parser.add_argument('--blender-old',default='C:/dev/tools/blender-4.2.0-windows-x64/blender.exe')
    parser.add_argument('--blender',default='C:/dev/tools/blender-4.5.11-windows-x64/blender.exe')
    args=parser.parse_args();ROOT.mkdir(exist_ok=True)
    if args.prepare:
        releases=api('repos/Sollumz/Sollumz/releases?per_page=100')
        selected=[r for r in releases if not r['draft'] and not r['prerelease']
                  and re.fullmatch(r'v\d+\.\d+\.\d+',r['tag_name'])
                  and tuple(map(int,r['tag_name'][1:].split('.'))) >= (2,7,0)]
        selected.sort(key=lambda r:tuple(map(int,r['tag_name'][1:].split('.'))))
        records=[prepare(r) for r in selected]
        (ROOT/'releases.json').write_text(json.dumps(records,indent=2))
    records=json.loads((ROOT/'releases.json').read_text())
    if args.versions:records=[r for r in records if r['version'] in args.versions.split(',')]
    results=[]
    if args.run:
        for entry in records:
            version=entry['version'];folder=ROOT/version
            exe=args.blender_old if version.startswith('2.7.') else args.blender
            env=os.environ.copy();env['BLENDER_USER_RESOURCES']=str(folder/'profile')
            env['PBR2GTA_MATRIX_RUN']=str(time.time_ns())
            output=folder/'result.json'
            if output.exists():output.rename(folder/('result-'+env['PBR2GTA_MATRIX_RUN']+'-previous.json'))
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
            with (folder/'launcher.log').open('w') as log:
                proc=subprocess.Popen([exe,'--factory-startup','--python',str(REPO/'tests/blender/compatibility_case.py'),
                                       '--',version],env=env,startupinfo=startup,stdout=log,stderr=log)
                try:proc.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    print(f'{version}: timed out; test process {proc.pid} needs inspection',flush=True)
                    results.append({'version':version,'passed':False,'pid':proc.pid,'error':'timeout'})
                    break
            result=json.loads(output.read_text()) if output.exists() else {'passed':False,'error':'No result; see launcher.log'}
            result['exit_code']=proc.returncode
            if proc.returncode != 0:
                result['passed']=False
                result['process_error']='Blender exited abnormally; inspect launcher.log and crash log.'
            results.append({'version':version,**result})
            print(version,'PASS' if result['passed'] else 'FAIL',flush=True)
        (ROOT/'matrix.json').write_text(json.dumps(results,indent=2))
        return 0 if len(results)==len(records) and all(r['passed'] for r in results) else 1
    print(json.dumps(records,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
