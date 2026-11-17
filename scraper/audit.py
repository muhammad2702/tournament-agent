"""Independent saved-file audit. No problem-statement extraction."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image
from .pdf_downloader import check_document


def audit(directory):
    root=Path(directory)
    failures=[];counts={'html':0,'assets':0,'pdfs':0,'indexes':0,'images_verified':0,'hashes_verified':0}
    for phase in ('discovery','html','pdf'):
        path=root/phase/'manifest.json'
        if not path.exists():
            failures.append({'phase':phase,'error':'Missing manifest'});continue
        manifest=json.loads(path.read_text())
        for url,record in manifest['resources'].items():
            if record['status']!='downloaded':
                failures.append({'phase':phase,'url':url,'error':record.get('error',record['status'])});continue
            try:
                file=root/phase/record['file'];body=file.read_bytes()
                if len(body)!=record['size_bytes']:raise ValueError('Size mismatch')
                if hashlib.sha256(body).hexdigest()!=record['sha256']:raise ValueError('SHA-256 mismatch')
                counts['hashes_verified']+=1
                if phase=='pdf':
                    if check_document(body)!=record['page_count']:raise ValueError('Page count mismatch')
                    counts['pdfs']+=1
                elif phase=='discovery':counts['indexes']+=1
                elif record['kind']=='html':counts['html']+=1
                else:
                    counts['assets']+=1
                    if record['content_type'].startswith('image/') and record['content_type']!='image/svg+xml':
                        with Image.open(file) as image:image.verify()
                        counts['images_verified']+=1
            except Exception as error:failures.append({'phase':phase,'url':url,'error':str(error)})
        for ref in manifest.get('references',[]):
            if ref.get('status') in {'pending','missing','failed'}:failures.append(dict(ref,phase=phase))
    inventory=json.loads((root/'inventory.json').read_text())
    for url,record in inventory['resources'].items():
        if record.get('status') not in {'downloaded','excluded'}:
            failures.append({'url':url,'error':'Inventory item not reconciled','status':record.get('status')})
    # HTTP aliases may have different URLs but identical bytes at the same path.
    missing=[]
    for phase in ('html','pdf'):
        m=json.loads((root/phase/'manifest.json').read_text())
        referenced={str(Path(record['file'])) for record in m['resources'].values() if record.get('file')}
        for file in (root/phase/'raw').rglob('*'):
            if file.is_file() and str(file.relative_to(root/phase)) not in referenced:missing.append(str(file.relative_to(root)))
    report={'counts':counts,'failures':failures,'unreferenced_files':missing,'complete':not failures and not missing,'language_uncertain_retained':[url for url,r in inventory['resources'].items() if r['kind'] in {'html','pdf'} and r['language_hint']=='uncertain' and r.get('status')=='downloaded']}
    (root/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return len(failures)+len(missing)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='tests/artifacts/downloads')
    raise SystemExit(bool(audit(parser.parse_args().output)))
