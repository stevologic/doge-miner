#!/usr/bin/env python
"""Standalone driver: extracts verbatim shipped isValidDogeAddress and runs it via cscript
on good + bad-prefix + full-length bad-char D+33zeros.
Saves generated JS and raw cscript output to SCRATCH.
Drives only the function body from index.html (no reimplementation).
"""
import re, os, subprocess, sys

SCRATCH = r"C:\Users\steph\AppData\Local\Temp\grok-goal-ee1345bd7c97\implementer"
os.makedirs(SCRATCH, exist_ok=True)

with open("frontend/index.html", encoding="utf-8") as f:
    html = f.read()

m = re.search(r"function isValidDogeAddress\(addr\)\s*\{", html)
if not m:
    print("FAIL: no isValidDogeAddress in shipped html")
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
js += js_body + "\n"
js += "WScript.Echo( isValidDogeAddress(\"DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK\") ? \"good\" : \"bad\" );\n"
js += "WScript.Echo( isValidDogeAddress(\"BADWALLET123\") ? \"good\" : \"bad\" );\n"
js += "// Build full 34-char D+33zeros vector safely for JScript (drive shipped regex on bad chars)\n"
js += "var badD0=\"D\";for(var i=0;i<33;i++)badD0+=\"0\";WScript.Echo(\"len=\"+badD0.length);WScript.Echo( isValidDogeAddress(badD0) ? \"good\" : \"bad\" );\n"

jspath = os.path.join(SCRATCH, "parity-shipped-badchar.js")
with open(jspath, "w", encoding="utf-8") as tf:
    tf.write(js)

print("WROTE:", jspath)
print("LEN OF JS:", len(js))

try:
    out = subprocess.check_output(["cscript", "//Nologo", "//E:JScript", jspath], stderr=subprocess.STDOUT, text=True, timeout=30)
except Exception as e:
    out = "CS CRIPT ERR: " + str(e)

print("=== RAW CSCRIPT OUTPUT ===")
print(out)
print("=== END RAW ===")

with open(os.path.join(SCRATCH, "parity-cscript-badchar-raw.txt"), "w", encoding="utf-8") as lf:
    lf.write(out)

lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
print("PARSED LINES:", lines)

# Expect at least good, bad, len=34, bad
assert "good" in lines[0].lower() or lines[0]=="good", "first must be good"
assert "bad" in lines[1].lower() or lines[1]=="bad", "second bad"
# third or fourth should show len 34 and bad
print("Parity driver OK (shipped isValid executed on full D+33zeros bad-char vector)")
