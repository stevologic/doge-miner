#!/usr/bin/env python
"""Standalone driver: extracts verbatim shipped applyEffortStats and runs it via cscript
on last LAUNCH_STATS JSON from capture.
Saves to SCRATCH/effort-dom-parity.txt
"""
import re, os, subprocess, sys

SCRATCH = r"C:\Users\steph\AppData\Local\Temp\grok-goal-85d73cdfd361\implementer"
os.makedirs(SCRATCH, exist_ok=True)

with open("frontend/index.html", encoding="utf-8") as f:
    html = f.read()

m = re.search(r"function applyEffortStats\(data\)\s*\{", html)
if not m:
    print("FAIL: no applyEffortStats in shipped html")
    sys.exit(2)
start = m.start()
depth = 0
started = False
i = start
end = start
while i < len(html):
    c = html[i]
    if c == "{":
        depth += 1
        started = True
    elif c == "}" and started:
        depth -= 1
        if depth == 0:
            end = i + 1
            break
    i += 1
js_body = html[start:end]

js = "if(!String.prototype.trim)String.prototype.trim=function(){return this.replace(/^\\s+|\\s+$/g,\"\");};\n"
js += "var els = {};\n"
js += "var document = {getElementById: function(id){ if(!els[id]) els[id]={style:{width:'0%'}, textContent:''}; return els[id]; }};\n"
js += js_body + "\n"
js += "var data = {\"effort_percent\":61.5,\"current_nonce\":\"0x12345678\",\"luck\":99.6,\"streak\":0,\"efficiency\":100.0,\"effort_text\":\"HASHING\"};\n"
js += "applyEffortStats(data);\n"
js += "WScript.Echo( 'effort-bar:' + els['effort-bar'].style.width );\n"
js += "WScript.Echo( 'effort-percent:' + els['effort-percent'].textContent );\n"
js += "WScript.Echo( 'effort-text:' + els['effort-text'].textContent );\n"

jspath = os.path.join(SCRATCH, "effort-parity.js")
with open(jspath, "w", encoding="utf-8") as tf:
    tf.write(js)

print("WROTE:", jspath)
print("LEN OF JS:", len(js))

try:
    out = subprocess.check_output(["cscript", "//Nologo", "//E:JScript", jspath], stderr=subprocess.STDOUT, text=True, timeout=30)
except Exception as e:
    out = "CSCRIPT ERR: " + str(e)

print("=== RAW CSCRIPT OUTPUT ===")
print(out)
print("=== END RAW ===")

with open(os.path.join(SCRATCH, "effort-dom-parity.txt"), "w", encoding="utf-8") as lf:
    lf.write(out)

print("DOM_PARITY saved")
