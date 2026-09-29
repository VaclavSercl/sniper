#!/usr/bin/python3
"""Encrypted PostgreSQL backups in private GitHub Releases, seven verified copies."""
import datetime, hashlib, json, os, pathlib, subprocess, tarfile, tempfile
REPO = 'VaclavSercl/beroun'
PREFIX = 'beroun-db-backup-'
MARKER = 'Managed encrypted Beroun database backup v1.'
os.environ['GH_CONFIG_DIR'] = '/home/wwwenda/.config/gh'
os.umask(0o077)
# HTTP/1.1 avoids a stalled upload observed with this host's HTTP/2 transport.
os.environ['GODEBUG'] = 'http2client=0'
def run(*args, **kw):
    kw.setdefault('timeout', 600)
    return subprocess.run(args, check=True, **kw)
def gh(*args):
    return run('gh', *args, capture_output=True, text=True).stdout
def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
meta=json.loads(gh('api',f'repos/{REPO}'))
if not meta['private']: raise RuntimeError('Backup target must remain private')
files=[p for p in pathlib.Path('/mnt/data/beroun/backups').glob('beroun_*.dump') if p.stat().st_size>0]
dump=max(files,key=lambda p:p.name)
if datetime.datetime.now().timestamp()-dump.stat().st_mtime>25*3600:
    raise RuntimeError('Local backup older than 25 hours')
tag=PREFIX+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
with tempfile.TemporaryDirectory(prefix='beroun-github-',dir='/var/lib') as tmp:
    work=pathlib.Path(tmp)
    roles=work/'roles.sql'
    with roles.open('wb') as out:
        run('runuser','-u','postgres','--','pg_dumpall','--roles-only',stdout=out)
    readme=work/'RESTORE.txt'
    readme.write_text('Decrypt using age -d -i recovery.agekey backup.tar.age > backup.tar\nExtract in a private directory. Restore roles.sql as postgres on a NEW server,\ncreate database beroun and restore database.dump using pg_restore --exit-on-error.\nroles.sql includes login roles; inspect it before applying to an existing server.\nThe recovery.agekey must be kept separately from GitHub.\n')
    archive=work/'backup.tar'
    with tarfile.open(archive,'w') as tf:
        tf.add(dump,arcname='database.dump');tf.add(roles,arcname='roles.sql');tf.add(readme,arcname='RESTORE.txt')
        tf.add('/opt/sniper/current/platform/runtime/scripts',arcname='operations/scripts')
        tf.add('/opt/sniper/current/platform/legacy',arcname='operations/repo',filter=lambda info: None if any(part in {'.git','__pycache__','.venv','venv'} for part in pathlib.PurePosixPath(info.name).parts) else info)
        for unit in pathlib.Path('/etc/systemd/system').glob('beroun-*'):
            if unit.is_file(): tf.add(unit,arcname='operations/systemd/'+unit.name)
        tf.add('/etc/systemd/system/alert@.service',arcname='operations/systemd/alert@.service')
    encrypted=work/'backup.tar.age'
    run('age','-R','/etc/beroun/backup/recipient.txt','-o',str(encrypted),str(archive))
    notes=work/'release-notes.txt'
    notes.write_text(MARKER+'\nSource: '+dump.name+'\nSHA256: '+digest(encrypted)+'\nEncrypted with age; recovery key is not uploaded.\n')
    gh('release','create',tag,'--repo',REPO,'--target',meta['default_branch'],'--draft','--title',tag,'--notes-file',str(notes))
    print('Uploading encrypted archive: '+str(encrypted.stat().st_size)+' bytes',flush=True)
    gh('release','upload',tag,str(encrypted),'--repo',REPO)
    print('Upload complete; downloading for verification',flush=True)
    downloads=work/'download';downloads.mkdir()
    gh('release','download',tag,'--repo',REPO,'--pattern','backup.tar.age','--dir',str(downloads))
    remote=downloads/'backup.tar.age'
    if digest(remote)!=digest(encrypted): raise RuntimeError('Downloaded archive checksum mismatch')
    decrypted=work/'verified.tar'
    run('age','-d','-i','/etc/beroun/backup/recovery.agekey','-o',str(decrypted),str(remote))
    if digest(decrypted)!=digest(archive): raise RuntimeError('Decryption verification failed')
    print('Downloaded and decrypted; restoring isolated test database',flush=True)
    testdb='beroun_remote_restore_'+str(os.getpid())
    run('runuser','-u','postgres','--','createdb','-T','template0',testdb)
    try:
        with tarfile.open(decrypted) as tf:
            dbfile=work/'remote.dump'
            with tf.extractfile('database.dump') as src, dbfile.open('wb') as dst:
                import shutil
                shutil.copyfileobj(src,dst)
        with dbfile.open('rb') as inp:
            run('runuser','-u','postgres','--','pg_restore','--exit-on-error','--single-transaction','-d',testdb,stdin=inp)
    finally:
        run('runuser','-u','postgres','--','dropdb','--if-exists',testdb)
    gh('release','edit',tag,'--repo',REPO,'--draft=false','--latest=false')
    # Only prune our explicitly marked, previously verified releases.
    pages=gh('api','--paginate',f'repos/{REPO}/releases?per_page=100','--jq','.[] | {tag_name,body,draft}')
    decoder=json.JSONDecoder();items=[]
    while pages.strip():
        obj,end=decoder.raw_decode(pages.lstrip());items.append(obj);pages=pages.lstrip()[end:]
    managed=sorted([r['tag_name'] for r in items if r['tag_name'].startswith(PREFIX) and r.get('body','').startswith(MARKER) and not r['draft']],reverse=True)
    for old in managed[7:]: gh('release','delete',old,'--repo',REPO,'--yes','--cleanup-tag')
print('GITHUB BACKUP PASS: uploaded, downloaded, decrypted and database restored; '+tag)
