import json, os, subprocess, sys, tempfile, time, base64, urllib.parse
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ORIGIN = os.environ.get("FUSED_RENDER_ORIGIN", "http://127.0.0.1:1777")
PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
OUT = os.environ.get("HAGAKI_SHOTS", tempfile.gettempdir())

class CDP:
    def __init__(self):
        r1, w1 = os.pipe(); r2, w2 = os.pipe()
        self.prof = tempfile.mkdtemp(prefix="hagaki-cdp-")
        def child():
            os.dup2(r1, 3); os.dup2(w2, 4)
        self.p = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={self.prof}",
                                   "--window-size=1440,1000", "--no-first-run", "--hide-scrollbars", "about:blank"],
                                  close_fds=False, preexec_fn=child, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(r1); os.close(w2)
        self.w = os.fdopen(w1, "wb", buffering=0); self.r = os.fdopen(r2, "rb", buffering=0)
        self.buf = b""; self.n = 0; self.events = []; self.sid = None
    def _read(self):
        while b"\0" not in self.buf:
            chunk = self.r.read(65536)
            if not chunk: raise RuntimeError("chrome closed")
            self.buf += chunk
        msg, _, self.buf = self.buf.partition(b"\0")
        return json.loads(msg)
    def call(self, method, params=None, session=True):
        self.n += 1; mid = self.n
        m = {"id": mid, "method": method, "params": params or {}}
        if session and self.sid: m["sessionId"] = self.sid
        self.w.write(json.dumps(m).encode() + b"\0")
        while True:
            msg = self._read()
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)
    def ev(self, js, wait=True):
        r = self.call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": wait})
        if "exceptionDetails" in r: return {"__error": r["exceptionDetails"].get("text"), "detail": str(r["exceptionDetails"].get("exception", {}).get("description"))[:400]}
        return r["result"].get("value")
    def until(self, js, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            v = self.ev(js)
            if v and not (isinstance(v, dict) and "__error" in v): return v
            time.sleep(0.4)
        return None
    def shot(self, name):
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        open(f"{OUT}/{name}.png", "wb").write(base64.b64decode(data))
    def errors(self):
        out = []
        for e in self.events:
            m = e.get("method")
            if m == "Runtime.exceptionThrown": out.append("EXC " + json.dumps(e["params"]["exceptionDetails"])[:400])
            if m == "Runtime.consoleAPICalled" and e["params"]["type"] in ("error", "warning"):
                out.append(e["params"]["type"] + " " + " ".join(str(a.get("value", a.get("description", "")))[:200] for a in e["params"]["args"]))
        return out
    def close(self):
        self.p.kill()

def url(**params):
    q = {"path": PAGE, **params}
    return f"{ORIGIN}/render?{urllib.parse.urlencode(q)}"

c = CDP()
t = c.call("Target.createTarget", {"url": "about:blank"}, session=False)
c.sid = c.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}, session=False)["sessionId"]
c.call("Runtime.enable"); c.call("Page.enable")
c.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
steps = json.loads(sys.argv[1])
try:
    for st in steps:
        if "go" in st:
            c.call("Page.navigate", {"url": url(**st["go"])})
            print("go", st["go"], "->", c.until(st.get("wait", "document.readyState==='complete'"), st.get("timeout", 30)))
        if "js" in st:
            print("js", st.get("label", ""), "->", json.dumps(c.ev(st["js"]), default=str)[:1500])
        if "until" in st:
            print("until", st.get("label", ""), "->", json.dumps(c.until(st["until"], st.get("timeout", 25)), default=str)[:800])
        if "sleep" in st: time.sleep(st["sleep"])
        if "shot" in st: c.shot(st["shot"]); print("shot", st["shot"])
    print("console errors/warnings:", c.errors() or "none")
finally:
    c.close()
