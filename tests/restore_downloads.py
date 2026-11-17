"""Recreate committed review artifacts from their source URLs and exact hashes."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from scraper.downloader import download

ROOT=Path('tests/artifacts/downloads')


def restore(url,path,record):
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']:
        return 'present',url
    content,_=download(url)
    if len(content)!=record['size_bytes'] or hashlib.sha256(content).hexdigest()!=record['sha256']:
        raise ValueError('Source bytes changed; original snapshot required: '+url)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp');temporary.write_bytes(content);temporary.replace(path)
    return 'restored',url


def main():
    jobs=[];paths=set();errors=[]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for phase in ('discovery','html','pdf'):
            manifest=json.loads((ROOT/phase/'manifest.json').read_text())
            for url,record in manifest['resources'].items():
                if record['status']!='downloaded':continue
                path=ROOT/phase/record['file']
                if path in paths:continue
                paths.add(path);jobs.append(workers.submit(restore,url,path,record))
        for job in as_completed(jobs):
            try:
                status,url=job.result();print(status,url,flush=True)
            except Exception as error:
                errors.append(str(error));print('ERROR',str(error),flush=True)
    (ROOT/'restore_report.json').write_text(json.dumps({'files_checked':len(paths),'failures':errors,'complete':not errors},indent=2))
    print('Files checked:',len(paths),'Failures:',len(errors),flush=True)
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
