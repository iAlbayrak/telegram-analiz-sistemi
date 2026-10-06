#!/usr/bin/env python3
"""Safely merge scanner state files and push without rebase conflicts."""
import json, shutil, subprocess, tempfile, time
from pathlib import Path

FILES = ['alert_state.json','scan_diagnostic.log','market_cap_cache.json','signal_outcomes.json']
ROOT = Path('.')
RANK = {'SKOR 80+':1,'SKOR 85+':2,'SKOR 90+':3,'🔵 GİRİLEBİLİR (80-90)':1,'🟢 ÇOK GÜÇLÜ (90+)':2,'🟢 ELİT (90+, tüm kapılar geçti)':3}

def run(*args, check=True):
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=True)

def read_json(path, default):
    try: return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception: return default

def merge_state(local, remote):
    out = dict(remote or {})
    for k, v in (local or {}).items():
        old = out.get(k)
        if not isinstance(old, dict): out[k] = v; continue
        lv, rv = int(v.get('last_seen',0) or 0), int(old.get('last_seen',0) or 0)
        if lv >= rv or RANK.get(v.get('tier'),0) > RANK.get(old.get('tier'),0): out[k] = v
    return out

def outcome_key(x):
    return (x.get('symbol'), x.get('direction'), x.get('signal_ts_ms'))

def merge_outcomes(local, remote):
    merged = {}
    for item in list(remote or []) + list(local or []):
        if not isinstance(item, dict): continue
        k = outcome_key(item)
        if k[0] is None: continue
        prev = merged.get(k)
        if prev is None:
            merged[k] = item; continue
        # Prefer resolved/newer observations over stale OPEN snapshots.
        p_res = 0 if prev.get('status') == 'OPEN' else int(prev.get('resolved_at_ms',0) or prev.get('last_checked_ms',0) or 0)
        i_res = 0 if item.get('status') == 'OPEN' else int(item.get('resolved_at_ms',0) or item.get('last_checked_ms',0) or 0)
        if i_res >= p_res:
            merged[k] = item
    return sorted(merged.values(), key=lambda x: int(x.get('signal_ts_ms',0) or 0))[-500:]

def merge_log(local_text, remote_text):
    lines=[]
    seen=set()
    for line in (remote_text.splitlines()+local_text.splitlines()):
        if not line or line in seen: continue
        seen.add(line); lines.append(line)
    return '\n'.join(lines[-500:])+'\n' if lines else ''

def merge_cache(local, remote):
    if not remote: return local or {}
    if not local: return remote
    return local if float(local.get('updated_at',0) or 0) >= float(remote.get('updated_at',0) or 0) else remote

def main():
    with tempfile.TemporaryDirectory(prefix='scanner-state-') as td:
        snap=Path(td)
        for name in FILES:
            src=ROOT/name
            if src.exists(): shutil.copy2(src, snap/name)
        for attempt in range(1,4):
            try:
                run('git','fetch','origin','main')
                run('git','reset','--hard','origin/main')
                # Merge the scan's local snapshot with the newest remote state.
                local_state=read_json(snap/'alert_state.json',{})
                remote_state=read_json(ROOT/'alert_state.json',{})
                (ROOT/'alert_state.json').write_text(json.dumps(merge_state(local_state,remote_state),ensure_ascii=False,indent=2),encoding='utf-8')

                local_out=read_json(snap/'signal_outcomes.json',[])
                remote_out=read_json(ROOT/'signal_outcomes.json',[])
                (ROOT/'signal_outcomes.json').write_text(json.dumps(merge_outcomes(local_out,remote_out),ensure_ascii=False,indent=2),encoding='utf-8')

                local_cache=read_json(snap/'market_cap_cache.json',{})
                remote_cache=read_json(ROOT/'market_cap_cache.json',{})
                (ROOT/'market_cap_cache.json').write_text(json.dumps(merge_cache(local_cache,remote_cache),ensure_ascii=False,indent=2),encoding='utf-8')

                local_log=(snap/'scan_diagnostic.log').read_text(encoding='utf-8') if (snap/'scan_diagnostic.log').exists() else ''
                remote_log=(ROOT/'scan_diagnostic.log').read_text(encoding='utf-8') if (ROOT/'scan_diagnostic.log').exists() else ''
                (ROOT/'scan_diagnostic.log').write_text(merge_log(local_log,remote_log),encoding='utf-8')

                run('git','add',*FILES)
                diff=run('git','diff','--cached','--quiet',check=False)
                if diff.returncode==0:
                    print('Kaydedilecek durum değişikliği yok.')
                    return 0
                run('git','commit','-m','V13.2 scanner state and diagnostics')
                push=run('git','push','origin','HEAD:main',check=False)
                if push.returncode==0:
                    print('Durum ve tanılar güvenli şekilde GitHub\'a kaydedildi.')
                    return 0
                print(f'Push çakıştı; yeniden senkronize ediliyor (deneme {attempt}/3).')
            except subprocess.CalledProcessError as exc:
                print(f'Git durum kaydı denemesi {attempt}/3 başarısız: {exc}')
            time.sleep(1.5*attempt)
    raise SystemExit('Durum dosyaları 3 denemede güvenli şekilde push edilemedi.')

if __name__=='__main__': main()
