#!/usr/bin/env python
import subprocess, time, urllib.request, json, os, sys
SCRATCH = r'C:\Users\steph\AppData\Local\Temp\grok-goal-85d73cdfd361\implementer'
os.makedirs(SCRATCH, exist_ok=True)
env = os.environ.copy()
env['PYTHONPATH'] = '.'
def do_launch(port, suffix, logf):
    with open(logf, 'w', encoding='utf-8') as lf:
        lf.write('=== LAUNCH ' + suffix + ' on ' + str(port) + ' ===\n')
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(port), '--log-level', 'info'], stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=os.getcwd())
        time.sleep(4)
        base = 'http://127.0.0.1:' + str(port)
        try:
            h = urllib.request.urlopen(base + '/api/health', timeout=5).read().decode()
            lf.write('HEALTH:' + h + '\n')
            p = json.dumps({'wallet':'DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK','mode':'cpu','workers':1}).encode()
            r = urllib.request.Request(base + '/api/start', data=p, headers={'Content-Type':'application/json'})
            lf.write('START:' + urllib.request.urlopen(r, timeout=10).read().decode() + '\n')
            for i in range(5):
                time.sleep(1.2)
                st = urllib.request.urlopen(base + '/api/stats', timeout=5).read().decode()
                lf.write('LAUNCH_STATS' + str(i) + ':' + st + '\n')
            rs = urllib.request.Request(base + '/api/stop', data=b'{}', headers={'Content-Type':'application/json'})
            lf.write('STOP:' + urllib.request.urlopen(rs, timeout=5).read().decode() + '\n')
            page = urllib.request.urlopen(base + '/', timeout=5).read().decode()
            open(os.path.join(SCRATCH, 'served-effort' + suffix + '.html'), 'w', encoding='utf-8').write(page)
            has = 'SCRYPT EFFORT' in page and 'id=\"effort-bar\"' in page
            lf.write('SERVED_HAS_EFFORT_DIV:' + str(has) + '\n')
        except Exception as e: lf.write('ERR:' + str(e) + '\n')
        finally:
            proc.terminate()
            time.sleep(1)
            lf.write('=== END LAUNCH ===\n')
    print('saved', logf)
log1 = os.path.join(SCRATCH, 'scrypt-effort-launch-1.log')
log2 = os.path.join(SCRATCH, 'scrypt-effort-launch-2.log')
do_launch(19470, '-1', log1)
time.sleep(1)
do_launch(19471, '-2', log2)
print('capture complete')
