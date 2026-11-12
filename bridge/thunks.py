"""Generic VI Server access through generated "thunk" VIs.

LabVIEW Property/Invoke Nodes are statically typed, so a single hand-built VI can't read or write an
arbitrary property.  Instead, the first time Python needs (class, property, direction) or
(class, method), it uses the scripting cases in LVBridge Server.vi to *build* a tiny VI that does
exactly that one thing, saves it under labview/thunks/, and from then on calls it directly through
ActiveX (set controls -> Run -> read indicators, well under 1 ms per call).

Thunk front-panel contract:
    ref    control   refnum of the target class (bridge handles are accepted as plain ints)
    value  control   (property write) value to write
    value  indicator (property read)  value read
    err    indicator error cluster (status, code, source)
    <param> controls / indicators named after the method's terminals (invoke thunks)

Server cases these rely on (see BUILD_GUIDE.md):
    pclass  h0=Property Node, s0="VI Server:<Class>"   set the node's class
    pwrite  h0=PropertyItem                              make the item a write item
    msel    h0=Invoke Node,   s0=<method ID string>      select the node's method
    mkctl / mkind / mkconst  h0=Terminal -> out_h[0]     Create Control / Indicator / Constant
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "bridge" / "vi_server_catalog.json"
METHODS = ROOT / "bridge" / "vi_server_methods.json"
THUNK_DIR = ROOT / "labview" / "thunks"

# Property Node terminal layout (verified): [0] ref in, [1] ref out, [2] error in, [3] error out, [4..] items
PN_REF_IN, PN_ERR_OUT, PN_ITEM0 = 0, 3, 4


class ThunkError(RuntimeError):
    pass


def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


class Catalog:
    """VI Server classes/properties dumped from this LabVIEW install (+ discovered method IDs)."""

    def __init__(self):
        self.cls = json.load(open(CATALOG, encoding="utf-8"))
        self._by_norm = {}
        for name, e in self.cls.items():
            for alias in (name, e.get("short"), e.get("data")):
                if alias:
                    self._by_norm.setdefault(_norm(alias), name)
        self.methods = json.load(open(METHODS, encoding="utf-8")) if METHODS.exists() else {}

    def save_methods(self):
        METHODS.write_text(json.dumps(self.methods, indent=1, sort_keys=True), encoding="utf-8")

    def class_name(self, cls):
        key = self._by_norm.get(_norm(cls))
        if not key:
            raise ThunkError(f"Unknown VI Server class '{cls}'")
        return key

    def prop(self, cls, name):
        """Find a property by long name, short name, or the last ':'-segment. Exact matches win."""
        cls = self.class_name(cls)
        props = self.cls[cls]["props"]
        n = _norm(name)
        for test in (lambda p: _norm(p["long"]) == n,
                     lambda p: _norm(p.get("short", "")) == n,
                     lambda p: _norm(str(p["long"]).split(":")[-1]) == n,
                     lambda p: n in _norm(p["long"])):
            hits = [p for p in props if test(p)]
            if hits:
                return hits[-1]  # subclass-specific entries come last
        raise ThunkError(f"{cls} has no property like '{name}'")

    def search(self, cls, query):
        cls = self.class_name(cls)
        q = query.lower()
        return [p for p in self.cls[cls]["props"] if q in str(p["long"]).lower()]


class Thunks:
    def __init__(self, bridge, thunk_dir=THUNK_DIR):
        self.b = bridge
        self.cat = Catalog()
        self.dir = Path(thunk_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._vis = {}      # key -> ActiveX VI
        self._meta = {}     # key -> manifest dict (invoke thunks)

    # ---------------- properties ----------------
    def get(self, h, cls, name):
        cls = self.cat.class_name(cls)
        p = self.cat.prop(cls, name)
        if not p["dir"] & 1:
            raise ThunkError(f"{cls}.{p['long']} is write-only")
        vi = self._prop_thunk(cls, p["uid"], write=False)
        return self._run(vi, {"ref": int(h)}, ["value"], f"get {cls}.{p['long']}")["value"]

    def set(self, h, cls, name, value):
        cls = self.cat.class_name(cls)
        p = self.cat.prop(cls, name)
        if not p["dir"] & 2:
            raise ThunkError(f"{cls}.{p['long']} is read-only")
        vi = self._prop_thunk(cls, p["uid"], write=True)
        self._run(vi, {"ref": int(h), "value": value}, [], f"set {cls}.{p['long']}")

    def _prop_thunk(self, cls, uid, write):
        key = f"{cls}.{uid}.{'w' if write else 'r'}"
        if key in self._vis:
            return self._vis[key]
        path = self._find(key)
        if path is None:
            path = self._build_prop(cls, uid, write, self.dir / f"{key}.vi")
        vi = self.b.lv.vi(str(path))
        self._vis[key] = vi
        return vi

    def _build_prop(self, cls, uid, write, path):
        b = self.b
        v = b.new_vi()
        pn = b.add_object(v["diagram"], "Property Node", 300, 150)
        b.cmd("pclass", h=[pn], s=[f"VI Server:{cls}"])
        b.set_properties(pn, [uid])
        if write:
            items = self.get(pn, "Property", "Property Items[]")
            b.cmd("pwrite", h=[items[0]])
        terms = [t["terminal"] for t in b.terminals(pn)]
        self._ref_input(v, pn, cls)
        b.set_label(b.cmd("mkind", h=[terms[PN_ERR_OUT]])["h"][0], "err")
        b.set_label(b.cmd("mkctl" if write else "mkind", h=[terms[PN_ITEM0]])["h"][0], "value")
        return self._save(v["vi"], path)

    def _is_gobject(self, cls):
        """GObject-family refnums can be passed through ActiveX as plain ints; others (VI,
        Application, Project...) arrive as COM objects and need a U32 + Type Cast input."""
        return any(p["uid"] == "6327806" for p in self.cat.cls[cls]["props"]) and             any(p["uid"] == "632a800" for p in self.cat.cls[cls]["props"])

    def _ref_input(self, v, node, cls):
        """Give `node`'s reference input a front-panel control labelled 'ref'."""
        b = self.b
        ref_in = b.terminals(node)[PN_REF_IN]["terminal"]
        if self._is_gobject(cls):
            b.set_label(b.cmd("mkctl", h=[ref_in])["h"][0], "ref")
            return
        D = v["diagram"]
        # refnum constant of exactly `cls`, borrowed from a throwaway Property Node
        tmp = b.add_object(D, "Property Node", 100, 320)
        b.cmd("pclass", h=[tmp], s=[f"VI Server:{cls}"])
        k = b.cmd("mkconst", h=[b.terminals(tmp)[PN_REF_IN]["terminal"]])["h"][0]
        tc = b.add_object(D, "Type Cast", 220, 150)
        tt = {self.get(x["terminal"], "Terminal", "Name"): x["terminal"] for x in b.terminals(tc)}
        src = [t for n, t in tt.items() if n not in ("x", "type")][0]
        b.wire(b.constant_terminal(k), tt["type"])
        b.wire(src, ref_in)
        self.call(tmp, "GObject", "Delete")
        self.call(D, "Diagram", "Remove Wire Loose Ends")
        u = b.add_object(v["panel"], "Numeric Control (modern)", 30, 30)
        self.set(u, "Numeric", "Representation", 6)  # U32
        b.set_label(u, "ref")
        b.wire(b.control_terminal(u), tt["x"])

    # ---------------- methods ----------------
    def call(self, h, cls, method, **args):
        """Invoke a VI Server method. args are keyed by parameter name (case/space-insensitive).
        Returns {output name: value}."""
        cls = self.cat.class_name(cls)
        mid = self.method_id(cls, method)
        key = f"{cls}.m{mid}"
        vi, meta = self._invoke_thunk(cls, mid, key)
        inputs = {"ref": int(h)}
        by_norm = {_norm(n): n for n in meta["inputs"]}
        for k, val in args.items():
            n = by_norm.get(_norm(k))
            if n is None:
                raise ThunkError(f"{cls}.{method}: no input '{k}'. Inputs: {meta['inputs']}")
            inputs[n] = val
        return self._run(vi, inputs, meta["outputs"], f"{cls}.{method}")

    def method_id(self, cls, method):
        ms = self.cat.methods.get(cls) or self.discover_methods(cls)
        n = _norm(method)
        for m in ms:
            if n in (_norm(m["name"]), _norm(m["short"]), _norm(m["name"].split(":")[-1])):
                return m["id"]
        hits = [m for m in ms if n in _norm(m["name"])]
        if len(hits) == 1:
            return hits[0]["id"]
        raise ThunkError(f"{cls} has no method '{method}'. Known: {[m['name'] for m in ms][:60]}")

    def discover_methods(self, cls):
        """Read Invoke.'All Supported Methods' on a scratch Invoke Node set to `cls`."""
        b = self.b
        v = b.new_vi()
        inv = b.add_object(v["diagram"], "Invoke Node", 300, 150)
        self.set(inv, "Invoke", "Invoke Node Class Name", f"VI Server:{cls}")
        raw = self.get(inv, "Invoke", "All Supported Methods")
        ms = self._parse_methods(raw)
        self.cat.methods[cls] = ms
        self.cat.save_methods()
        return ms

    @staticmethod
    def _parse_methods(raw):
        """'All Supported Methods' -> array of (ID, short name, data name, long name)."""
        return [{"id": r[0], "short": r[1], "name": r[3]} for r in (raw or ())]

    def _invoke_thunk(self, cls, mid, key):
        if key in self._vis:
            return self._vis[key], self._meta[key]
        path = self._find(key)
        mpath = self.dir / f"{key}.json"
        if path is None or not mpath.exists():
            path = self._build_invoke(cls, mid, self.dir / f"{key}.vi", mpath)
        meta = json.loads(mpath.read_text(encoding="utf-8"))
        vi = self.b.lv.vi(str(path))
        self._vis[key], self._meta[key] = vi, meta
        return vi, meta

    def _build_invoke(self, cls, mid, path, mpath):
        b = self.b
        v = b.new_vi()
        inv = b.add_object(v["diagram"], "Invoke Node", 300, 150)
        self.set(inv, "Invoke", "Invoke Node Class Name", f"VI Server:{cls}")
        b.cmd("msel", h=[inv], s=[mid])
        meta = {"class": cls, "id": mid, "inputs": [], "outputs": []}
        used = set()

        def label_for(term, default):
            name = self.get(term, "Terminal", "Name") or default
            low = name.lower()
            if low in ("reference", "reference in"):
                return "ref"
            if low.startswith("error in"):
                return "err in"
            if low.startswith("error out"):
                return "err"
            if low.startswith("reference out") or low == "dup reference":
                return "ref out"
            base, i = name, 2
            while name in used:
                name = f"{base} {i}"
                i += 1
            return name

        for t in self.get(inv, "Invoke", "Input Terminals []") or ():
            name = label_for(t, "in")
            used.add(name)
            b.set_label(b.cmd("mkctl", h=[t])["h"][0], name)
            if name not in ("ref", "err in"):
                meta["inputs"].append(name)
        for t in self.get(inv, "Invoke", "Output Terminals []") or ():
            name = label_for(t, "out")
            used.add(name)
            b.set_label(b.cmd("mkind", h=[t])["h"][0], name)
            if name not in ("ref out",):
                meta["outputs"].append(name)
        # Input/Output Terminals [] exclude reference and error; they sit at the same fixed slots
        # as on a Property Node.
        terms = [t["terminal"] for t in b.terminals(inv)]
        self._ref_input(v, inv, cls)
        b.set_label(b.cmd("mkind", h=[terms[PN_ERR_OUT]])["h"][0], "err")
        path = self._save(v["vi"], path)
        mpath.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return path

    # ---------------- primitives ----------------
    def prim(self, style, **inputs):
        """Run any palette function (New VI Object style name, e.g. "Open VI Reference") once.
        Only the inputs you pass are wired (by terminal name, fuzzy); others keep their defaults.
        Every output becomes an indicator (reference outputs are returned as U32 handles).
        Returns {output name: value}."""
        names = sorted(_norm(k) for k in inputs)
        key = "prim." + re.sub(r"[^A-Za-z0-9]+", "_", style).strip("_") + ("." + "_".join(names) if names else "")
        key = key[:120]
        if key not in self._vis:
            path = self._find(key)
            mpath = self.dir / f"{key}.json"
            if path is None or not mpath.exists():
                path = self._build_prim(style, list(inputs), self.dir / f"{key}.vi", mpath)
            self._meta[key] = json.loads(mpath.read_text(encoding="utf-8"))
            self._vis[key] = self.b.lv.vi(str(path))
        vi, meta = self._vis[key], self._meta[key]
        for k, v in inputs.items():
            vi.set_control(meta["map"][k], v)
        vi.call("Run", False)
        out = {k: vi.get_control(k) for k in meta["outputs"]}
        err = next((v for k, v in out.items() if k.lower().startswith("error out")), None)
        if err and err[0]:
            raise ThunkError(f"{style}: LabVIEW error {err[1]}: {err[2]}")
        return out

    def _build_prim(self, style, wanted, path, mpath):
        b = self.b
        v = b.new_vi()
        node = b.add_object(v["diagram"], style, 300, 150)
        meta = {"style": style, "inputs": [], "outputs": [], "map": {}}
        terms = []
        for t in (x["terminal"] for x in b.terminals(node)):
            terms.append((t, self.get(t, "Terminal", "Name") or "t", bool(self.get(t, "Terminal", "Is Source?"))))
        ins = [(t, n) for t, n, src in terms if not src]
        for k in wanted:  # resolve requested inputs to terminals
            kn = _norm(k)
            hit = next((x for x in ins if _norm(x[1]) == kn), None) or                 next((x for x in ins if kn in _norm(x[1])), None)
            if hit is None:
                raise ThunkError(f"{style}: no input like '{k}'. Inputs: {[n for _, n in ins]}")
            h = b.cmd("mkctl", h=[hit[0]])["h"][0]
            b.set_label(h, hit[1])
            meta["inputs"].append(hit[1])
            meta["map"][k] = hit[1]
        row = 0
        for t, name, src in terms:
            if not src:
                continue
            row += 1
            low = name.lower()
            try:
                if ("reference" in low or "refnum" in low) and "error" not in low:
                    # VI/App refnums come back through ActiveX as COM objects; hand out U32 handles
                    tc = b.add_object(v["diagram"], "Type Cast", 500, 100 + 60 * row)
                    u32 = b.add_object(v["diagram"], "Numeric Constant", 420, 130 + 60 * row)
                    b.set_representation(u32, "u32")
                    tt = [(x["terminal"], self.get(x["terminal"], "Terminal", "Name"),
                           bool(self.get(x["terminal"], "Terminal", "Is Source?"))) for x in b.terminals(tc)]
                    tsrc = next(x for x, _, s in tt if s)
                    by = {n: x for x, n, s in tt if not s}
                    b.wire(t, by.get("x", list(by.values())[0]))
                    b.wire(b.constant_terminal(u32), by.get("type", list(by.values())[-1]))
                    h = b.cmd("mkind", h=[tsrc])["h"][0]
                else:
                    h = b.cmd("mkind", h=[t])["h"][0]
            except Exception:  # noqa: BLE001
                continue
            b.set_label(h, name)
            meta["outputs"].append(name)
        path = self._save(v["vi"], path)
        mpath.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return path

    # ---------------- files ----------------
    def _find(self, key):
        """Newest saved thunk for key (key.vi, key~2.vi, ...)."""
        files = sorted(self.dir.glob(f"{key}.vi")) + sorted(self.dir.glob(f"{key}~*.vi"))
        return max(files, key=lambda f: f.stat().st_mtime) if files else None

    def _save(self, vi_handle, path):
        """Save; if a VI with that path is still loaded in LabVIEW (e.g. a deleted stale thunk),
        fall back to key~N.vi."""
        path = Path(path)
        for i in range(1, 50):
            p = path if i == 1 else path.with_name(f"{path.stem}~{i}{path.suffix}")
            if p.exists():
                continue
            try:
                self.b.save(vi_handle, str(p))
                return p
            except Exception as e:  # noqa: BLE001
                if "1357" not in str(e):
                    raise
        raise ThunkError(f"could not save thunk {path}")

    # ---------------- run ----------------
    def _run(self, vi, inputs, outputs, what):
        for k, val in inputs.items():
            vi.set_control(k, val)
        vi.call("Run", False)
        err = vi.get_control("err")
        if err and err[0]:
            raise ThunkError(f"{what}: LabVIEW error {err[1]}: {err[2]}")
        return {k: vi.get_control(k) for k in outputs}
