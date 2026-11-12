"""High-level, text-friendly LabVIEW scripting API.

Everything here composes the mailbox commands of LVBridge Server.vi (object creation, wiring, save)
with generated thunks (any property / any method, see thunks.py).  It is designed so a whole VI can be
built from one short Python script, e.g.:

    vi = lv.new_vi()
    a  = vi.control("Numeric", "a", 50, 50)
    s  = vi.indicator("Numeric", "sum", 400, 50)
    add = vi.diagram.add("Add", 200, 60)
    wire(a, add["x"]); wire(add["y"], vi.diagram.const(2.5)); wire(add["x+y"], s)
    vi.save(r"C:/temp/demo.vi")

Objects:
    Obj        any VI Server object:  o["Prop"], o["Prop"] = v, o.call("Method", Arg=...)
    Node       diagram node:          node["x"] / node[0] -> Terminal, node.terms
    Terminal   wire endpoint:         t.name, t.is_source, t.wire(other)
    Control    front-panel control:   c.label, c.term (diagram terminal), c.indicator
    Diagram    diagram/subdiagram:    d.add(style), d.subvi(path), d.const(value), d.nodes(), d.find(...)
    Structure  Case/Event/Loop node:  s.frames, s.frame("name"), s.add_frame(...), s.names
    VI         vi.diagram, vi.panel, vi.controls(), vi.ctl("label"), vi.save(path)
"""
import os
import re

from client import Bridge, BridgeError
from thunks import Thunks, ThunkError, _norm

# LabVIEW Representation enum
REPS = {"ext": 0, "dbl": 1, "sgl": 2, "i32": 3, "i16": 4, "i8": 5, "u32": 6, "u16": 7,
        "u8": 8, "cxt": 9, "cdb": 10, "csg": 11, "i64": 12, "u64": 13}

STYLE_ALIASES = {
    # short names -> New VI Object style names
    "numeric": "Numeric Control (modern)", "numeric control": "Numeric Control (modern)",
    "numeric indicator": "Numeric Indicator (modern)",
    "case": "Case Structure", "event": "Event Structure", "while": "While Loop #1", "while loop": "While Loop #1", "for": "For Loop",
    "waveform chart": "Waveform Chart (modern)", "waveform graph": "Waveform Graph (modern)",
    "round led": "Round LED (modern)", "square led": "Square LED (modern)", "led": "Round LED (modern)",
    "string control": "String Control (modern)", "string indicator": "String Indicator (modern)",
}


class LV:
    """Session: owns the mailbox Bridge and the thunk engine."""

    def __init__(self, bridge=None):
        self.b = bridge or Bridge()
        self.t = Thunks(self.b)

    # -- factories --
    def new_vi(self):
        h = self.b.new_vi()
        return VI(self, h["vi"], h["diagram"], h["panel"])

    def open_vi(self, path):
        path = os.path.abspath(path)
        h = self.b.open_vi(path)
        if not h["panel"]:
            h["panel"] = self.t.get(h["vi"], "VI", "Front Panel")
        return VI(self, h["vi"], h["diagram"], h["panel"], path)

    CTL_KIND = {"control": 0, "typedef": 1, "strict": 2}
    _CTL_SEED = os.path.join("vi.lib", "ActorFramework", "Message Dequeuer", "Dequeue Priority.ctl")

    def new_ctl(self, path, style, label=None, kind="typedef"):
        """Create a new .ctl file holding one control of `style` (kind: control | typedef | strict).
        Saving a new VI with a .ctl name gives a VI, not a control file, so this copies a tiny
        vi.lib .ctl as a seed, swaps its control and saves under the new name."""
        import shutil
        path = os.path.abspath(path)
        if os.path.exists(path):
            raise FileExistsError(path)
        shutil.copy(os.path.join(self.b.lv.app.get("ApplicationDirectory"), self._CTL_SEED), path)
        t = self.open_vi(path)
        try:  # the seed is a member of Actor Framework.lvlib; the copy must not be
            t.call("Disconnect From Library")
        except ThunkError:
            pass
        old = t.controls()
        c = t._place(style, label, 0, 0, None)
        for o in old:
            o.delete()
        t.set("Control VI Type", self.CTL_KIND.get(kind, kind), "VI")
        t.save()
        t.root = c
        return t

    def obj(self, h, cls=None):
        """Wrap a raw handle in the most useful Python class (detects VI Server class)."""
        if isinstance(h, Obj):
            return h
        h = int(h)
        if not h:
            return None
        cls = cls or self.class_of(h)
        if cls in ("Terminal",) or cls.endswith("Terminal") and cls != "ControlTerminal":
            return Terminal(self, h, cls)
        if cls in ("Diagram",):
            return Diagram(self, h, cls)
        if self._isa(cls, "Structure"):
            return Structure(self, h, cls)
        if self._isa(cls, "Node"):
            return Node(self, h, cls)
        if self._isa(cls, "Control"):
            return Control(self, h, cls)
        return Obj(self, h, cls)

    def class_of(self, h):
        return self.t.get(h, "GObject", "Class Name")

    _ISA = {
        "Structure": {"Structure", "CaseStructure", "EventStructure", "WhileLoop", "ForLoop", "Loop",
                      "SequenceStructure", "FlatSequence", "StackedSequence", "TimedLoop",
                      "DisableStructure", "ConditionalDisableStructure", "TypeSpecializationStructure",
                      "InPlaceElementStructure", "DiagramDisableStructure"},
    }

    def _isa(self, cls, base):
        if base in self._ISA:
            return cls in self._ISA[base] or cls.endswith(("Structure", "Loop", "Sequence"))
        if base == "Node":
            # anything that has a Terminals[] property behaves like a node
            try:
                return self.t.cat.prop(cls, "Terminals[]")["long"] == "Terminals[]"
            except ThunkError:
                return False
        if base == "Control":
            try:
                p = self.t.cat.prop(cls, "Indicator")
                return p["long"] == "Indicator"
            except ThunkError:
                return False
        return False

    _express = None

    def express_path(self, name):
        """Path of the Express VI instance file for a palette name ("Elapsed Time")."""
        if self._express is None:
            root = os.path.join(self.b.lv.app.get("ApplicationDirectory"), "vi.lib", "express")
            self._express = {}
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    if f.startswith("Ex_Inst_") and f.endswith(".vi"):
                        self._express[_norm(f[8:-3])] = os.path.join(dp, f)
        key = _norm(name)
        if key in self._express:
            return self._express[key]
        hits = [v for k, v in self._express.items() if key in k]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"no Express VI {name!r}; candidates: {sorted(self._express)[:40]}")

    # -- user hand-off notes --
    todos = None

    def todo(self, task, where=""):
        """Record a step the bridge can't script (VI.todo also drops a note in the VI)."""
        self.todos = self.todos or []
        self.todos.append((where, task))

    def flush_todos(self, folder):
        """Write TODO_USER.md (checklist of manual steps) into folder; returns its path or None."""
        if not self.todos:
            return None
        path = os.path.join(folder, "TODO_USER.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Manual steps left for you\n\nThe build script could not do these; "
                    "each is also marked with a 'USER TODO' note in the VI.\n\n")
            for where, task in self.todos:
                f.write(f"- [ ] **{where}**: {task}\n" if where else f"- [ ] {task}\n")
        return path

    # -- discovery helpers (for the model / MCP) --
    def props(self, cls, query=""):
        return [(p["long"], {1: "r", 2: "w", 3: "rw"}[p["dir"]]) for p in self.t.cat.search(cls, query)]

    def methods(self, cls, query=""):
        cls = self.t.cat.class_name(cls)
        ms = self.t.cat.methods.get(cls) or self.t.discover_methods(cls)
        return [m["name"] for m in ms if query.lower() in m["name"].lower()]

    def args(self, cls, method):
        """Input/output parameter names of a method (builds its thunk)."""
        cls = self.t.cat.class_name(cls)
        mid = self.t.method_id(cls, method)
        _, meta = self.t._invoke_thunk(cls, mid, f"{cls}.m{mid}")
        return {"inputs": meta["inputs"], "outputs": meta["outputs"]}


class Obj:
    def __init__(self, lv, h, cls):
        self.lv, self.h, self.cls = lv, int(h), cls

    def __int__(self):
        return self.h

    def __eq__(self, other):
        return isinstance(other, Obj) and other.h == self.h or other == self.h

    def __hash__(self):
        return self.h

    def __repr__(self):
        try:
            lab = self.label
        except Exception:  # noqa: BLE001
            lab = None
        return f"<{self.cls} {self.h}{' ' + repr(lab) if lab else ''}>"

    # generic property / method access
    def __getitem__(self, prop):
        return self.get(prop)

    def __setitem__(self, prop, value):
        self.set(prop, value)

    def get(self, prop, cls=None):
        return self.lv.t.get(self.h, cls or self.cls, prop)

    def set(self, prop, value, cls=None):
        self.lv.t.set(self.h, cls or self.cls, prop, value)
        return self

    def call(self, method, cls=None, **args):
        out = self.lv.t.call(self.h, cls or self.cls, method, **args)
        return out

    def wrap(self, h, cls=None):
        return self.lv.obj(h, cls)

    @property
    def label(self):
        return None

    @property
    def owner(self):
        return self.wrap(self.get("Owner", "GObject"))

    @property
    def pos(self):
        return self.get("Position", "GObject")

    @pos.setter
    def pos(self, xy):
        self.set("Position", tuple(int(v) for v in xy), "GObject")

    @property
    def bounds(self):
        return self.get("Bounds", "GObject")

    def delete(self):
        self.call("Delete", cls="GObject")


class Terminal(Obj):
    @property
    def name(self):
        return self.get("Name", "Terminal")

    @property
    def is_source(self):
        return bool(self.get("Is Source?", "Terminal"))

    @property
    def label(self):
        return self.name

    def wire(self, other):
        wire(self, other)
        return self

    def unwire(self):
        """Delete the wire connected to this terminal (the whole wire net in that diagram)."""
        w = self.get("Connected Wire", "Terminal")
        if w:
            self.lv.t.call(w, "Wire", "Delete")
        return self

    @property
    def wired(self):
        """True/False, or None when this terminal type doesn't expose 'Connected Wire'."""
        try:
            return bool(self.get("Connected Wire", "Terminal"))
        except ThunkError:
            return None

    def control(self, label=None):
        c = self.wrap(self.lv.b.cmd("mkctl", h=[self.h])["h"][0])
        if label:
            c.label = label
        return c

    def indicator(self, label=None):
        c = self.wrap(self.lv.b.cmd("mkind", h=[self.h])["h"][0])
        if label:
            c.label = label
        return c

    def constant(self, value=None):
        c = self.wrap(self.lv.b.cmd("mkconst", h=[self.h])["h"][0])
        if self.wired is False:  # Create Constant doesn't auto-wire on tunnel inside terminals
            wire(c, self)
        if value is not None:
            set_constant(c, value)
        return c


class Node(Obj):
    _terms = None

    @property
    def terms(self):
        if self._terms is None:
            self._terms = [Terminal(self.lv, t["terminal"], "Terminal") for t in self.lv.b.terminals(self.h)]
        return self._terms

    def refresh(self):
        self._terms = None
        return self

    def inp(self, key):
        """Input terminal by (fuzzy) name."""
        return self.term(key, source=False)

    def out(self, key=None):
        """Output terminal by (fuzzy) name; with no key, the only/first output."""
        if key is None:
            return next(t for t in self.terms if t.name and t.is_source)
        return self.term(key, source=True)

    def term(self, key, source=None):
        if isinstance(key, int):
            return self.terms[key]
        n = _norm(key)
        names = [(t, t.name) for t in self.terms if t.name]
        if source is not None:
            names = [(t, nm) for t, nm in names if t.is_source == source]
        for t, nm in names:
            if _norm(nm) == n:
                return t
        for pick in (lambda nm: _norm(nm).startswith(n),                       # "file" -> "file (use dialog)"
                     lambda nm: _norm(re.sub(r"\(.*?\)", "", nm)) == n,         # ignore "(default)" notes
                     lambda nm: n in _norm(nm)):
            hits = [t for t, nm in names if n and pick(nm)]
            if len(hits) == 1:
                return hits[0]
        raise KeyError(f"{self.cls}: no unique terminal '{key}'. Terminals: {[nm for _, nm in names]}")

    def __getitem__(self, key):
        """node["x"] / node[0] -> Terminal;  node["@Prop"] -> property value."""
        if isinstance(key, str) and key.startswith("@"):
            return self.get(key[1:])
        return self.term(key)

    def __setitem__(self, key, value):
        if isinstance(key, str) and key.startswith("@"):
            key = key[1:]
        self.set(key, value)

    @property
    def label(self):
        try:
            return self.wrap(self.get("Label", "Node"), "Text").get("Text", "Text")
        except Exception:  # noqa: BLE001
            return None

    def term_names(self):
        return [t.name for t in self.terms]


class Structure(Node):
    @property
    def frames(self):
        key = "Frames[]" if self._has("Frames[]") else "Diagrams[]"
        return [Diagram(self.lv, h, "Diagram") for h in self.get(key)]

    def _has(self, prop):
        try:
            self.lv.t.cat.prop(self.cls, prop)
            return prop in [p["long"] for p in self.lv.t.cat.cls[self.cls]["props"]]
        except ThunkError:
            return False

    @property
    def names(self):
        return [n.strip().strip('"') for n in self.get("Frame Names")]

    @names.setter
    def names(self, names):
        self.set("Frame Names", tuple(names))

    def frame(self, key):
        if isinstance(key, int):
            return self.frames[key]
        names = self.names
        n = key.strip().strip('"')
        for i, nm in enumerate(names):
            if nm == n:
                return self.frames[i]
        raise KeyError(f"no frame {key!r}; frames: {names}")

    def add_frame(self, name=None, after=None):
        """Append a frame (after the last, or after frame index/name `after`); optionally name it."""
        idx = len(self.frames) - 1 if after is None else (after if isinstance(after, int) else self.names.index(after))
        self.call("Add Frame", **{"Reference Frame Index": idx, "After? (T)": True})
        if name is not None and self._has("Frame Names"):
            names = self.names
            names[idx + 1] = name
            self.names = [f'"{x}"' if not x.startswith('"') and self._string_selector() else x for x in names]
        return self.frames[idx + 1]

    def _string_selector(self):
        return all(n.startswith('"') for n in self.get("Frame Names")) if self.cls == "CaseStructure" else False

    @property
    def diagram(self):
        return self.frames[0]

    def new_tunnel(self, fn):
        """Run fn() (a wire that crosses this structure's border) and return the tunnel it created."""
        before = {int(t.h) for t in self.tunnels}
        fn()
        made = [t for t in self.tunnels if t.h not in before]
        if len(made) != 1:
            raise RuntimeError(f"expected 1 new tunnel, got {len(made)}")
        return made[0]

    def wire_out(self, dst, sources):
        """One output tunnel to `dst` fed from every frame. sources: {frame name/index/Diagram: source}.
        Wiring each frame straight to dst would make one tunnel per frame (a broken VI), so the first
        wire makes the tunnel and the rest wire to its inside terminals."""
        items = list(sources.items())
        frame = lambda k: k if isinstance(k, Diagram) else self.frame(k)  # noqa: E731
        t = self.new_tunnel(lambda: wire(items[0][1], dst))
        for k, src in items[1:]:
            wire(src, t.inside(frame(k)))
        return t

    # -- loops --
    def add_shift_register(self, y=40):
        """Add a shift register pair; returns ShiftRegister (left/right terminals in & out)."""
        h = self.call("Add Shift Register", **{"Y Position": int(y)})["Add Shift Register"]
        return ShiftRegister(self.lv, h, self)

    @property
    def stop_terminal(self):
        """Conditional terminal of a While Loop (wire a Boolean here)."""
        return Terminal(self.lv, self.get("Loop End Ref", "WhileLoop"), "Terminal")

    def resize(self, w, h):
        self.set("Frame Size", (int(w), int(h)), "WhileLoop" if self.cls == "WhileLoop" else self.cls)

    @property
    def tunnels(self):
        return [Tunnel(self.lv, t, self.lv.class_of(t)) for t in self.get("Tunnels[]", "Structure")]

    def tunnel_at(self, side, y, tol=12):
        """Tunnel on the 'left'/'right' border whose top is near y (structure coordinates)."""
        x0 = self.pos[0]
        ts = [t for t in self.tunnels if (t.pos[0] <= x0 + 5) == (side == "left")]
        best = min(ts, key=lambda t: abs(t.pos[1] - y))
        if abs(best.pos[1] - y) > tol:
            raise KeyError(f"no {side} tunnel near y={y}: {[t.pos for t in ts]}")
        return best


class Tunnel(Obj):
    def inside(self, frame=0):
        """Inside terminal on a given frame (index or Diagram). Inside Terminals[] is NOT in frame
        display order after frames are added, so match each terminal's owning diagram."""
        terms = [Terminal(self.lv, t, "Terminal") for t in self.get("Inside Terminals[]", "Tunnel")]
        if len(terms) == 1:
            return terms[0]
        if isinstance(frame, int):
            struct = self.lv.obj(self.get("Owner", "GObject"))
            frame = struct.frames[frame]
        fh = int(frame)
        for t in terms:
            if t.get("Diagram", "Terminal") == fh:
                return t
        raise KeyError("tunnel has no inside terminal on that frame")

    @property
    def outside(self):
        return Terminal(self.lv, self.get("Outside Terminal", "Tunnel"), "Terminal")


class ShiftRegister(Obj):
    """Shift register pair. LabVIEW only returns the right half, so the left terminals are found by
    position on the loop. init: outside-left input; read: inside-left (this iteration's value);
    write: inside-right input (next iteration); final: outside-right output."""
    def __init__(self, lv, right_h, loop):
        super().__init__(lv, right_h, "RightShiftRegister")
        self.loop = loop

    def _y(self):
        return self.get("Outside Terminal", "RightShiftRegister") and             Terminal(self.lv, self.get("Outside Terminal", "RightShiftRegister"), "Terminal").pos[1]

    @property
    def write(self):
        return Terminal(self.lv, self.get("Inside Terminals[]", "RightShiftRegister")[0], "Terminal")

    @property
    def final(self):
        return Terminal(self.lv, self.get("Outside Terminal", "RightShiftRegister"), "Terminal")

    @property
    def init(self):
        y, x0 = self._y(), self.loop.pos[0]
        for t in self.loop.get("Terminals[]", "Node"):
            T = Terminal(self.lv, t, "Terminal")
            p = T.pos
            if abs(p[1] - y) <= 2 and p[0] <= x0 + 2 and not T.is_source:
                return T
        raise KeyError("left shift register (outside) not found")

    @property
    def read(self):
        left = self.init.get("Owner", "GObject")  # LeftShiftRegister
        return Terminal(self.lv, self.lv.t.get(left, "LeftShiftRegister", "Inside Terminals[]")[0], "Terminal")


class Control(Obj):
    @property
    def label(self):
        return self.wrap(self.get("Label", "Control"), "Text").get("Text", "Text")

    @label.setter
    def label(self, text):
        self.lv.b.set_label(self.h, text)

    @property
    def term(self):
        return Terminal(self.lv, self.lv.b.control_terminal(self.h), "Terminal")

    @property
    def indicator(self):
        return bool(self.get("Indicator", "Control"))

    @indicator.setter
    def indicator(self, v):
        self.set("Indicator", bool(v), "Control")

    # -- clusters (front panel or inside a .ctl) --
    def children(self):
        """Elements of a cluster control, in cluster order."""
        return [self.wrap(h) for h in self.get("Controls[]", "Cluster")]

    def child(self, label):
        for c in self.children():
            if c.label == label:
                return c
        raise KeyError(f"no element {label!r}; have {[c.label for c in self.children()]}")

    def add(self, style_or_path, label=None):
        """Add an element to this cluster: a style name, or a path to a .ctl (typedef instance)."""
        if str(style_or_path).lower().endswith((".ctl", ".vi")):
            h = self.lv.b.add_subvi(self.h, os.path.abspath(style_or_path), 0, 0)
        else:
            h = self.lv.b.add_object(self.h, STYLE_ALIASES.get(str(style_or_path).lower(), style_or_path), 0, 0)
        c = self.wrap(h)
        if label:
            c.label = label
        self.arrange()
        return c

    def arrange(self, vertical=True):
        """Cluster Auto Sizing: 3 = arrange vertically, 2 = horizontally."""
        self.set("Auto Sizing", 3 if vertical else 2, "Cluster")

    # -- common control settings --
    MECH = {"switch when pressed": 0, "switch when released": 1, "switch until released": 2,
            "latch when pressed": 3, "latch when released": 4, "latch until released": 5}

    @property
    def mechanical_action(self):
        return self.get("Mechanical Action", "Boolean")

    @mechanical_action.setter
    def mechanical_action(self, v):
        self.set("Mechanical Action", self.MECH.get(str(v).lower(), v), "Boolean")

    @property
    def items(self):
        """Enum item strings."""
        return list(self.get("Strings []", "Enum"))

    @items.setter
    def items(self, names):
        self.set("Strings []", tuple(names), "Enum")


class Diagram(Obj):
    def add(self, style, x=100, y=100, label=None):
        style = STYLE_ALIASES.get(str(style).lower(), style)
        o = self.wrap(self.lv.b.add_object(self.h, style, int(x), int(y)))
        if label:
            set_node_label(o, label)
        return o

    def subvi(self, path, x=100, y=100):
        return self.wrap(self.lv.b.add_subvi(self.h, os.path.abspath(path), int(x), int(y)))

    def express(self, name, x=100, y=100):
        """Express VI by palette name, e.g. "Elapsed Time", "Time Delay" (default configuration).
        Placing vi.lib/express/**/Ex_Inst_<name>.vi by path yields a real Express VI node."""
        return self.subvi(self.lv.express_path(name), x, y)

    def note(self, text, x=20, y=20):
        """Free label (comment) on this diagram."""
        lab = self.wrap(self.lv.b.add_object(self.h, "Free Label", int(x), int(y)), "Text")
        lab.set("Text", text, "Text")
        return lab

    def const(self, value, x=100, y=100, rep=None):
        """Typed constant: bool, int (I32), float (DBL), str, or (list) -> array via Value."""
        if isinstance(value, bool):
            c = self.add("Boolean Constant", x, y)
        elif isinstance(value, (int, float)):
            c = self.add("Numeric Constant", x, y)
        elif isinstance(value, str):
            c = self.add("String Constant", x, y)
        else:
            raise TypeError("const(): use bool/int/float/str; for other types create a terminal constant")
        set_constant(c, value, rep)
        return c

    def nodes(self):
        return [self.wrap(h) for h in self.get("Nodes[]", "Diagram")]

    def clear(self, keep=()):
        """Delete every node in this (sub)diagram except `keep`, then trim loose wire ends."""
        keep = {int(k) for k in keep}
        for n in self.nodes():
            if n.h not in keep:
                n.delete()
        self.lv.t.call(self.h, "Diagram", "Remove Wire Loose Ends")

    def objects(self):
        return [self.wrap(h) for h in self.get("All Objects[]", "Diagram")]

    def find(self, label=None, cls=None, recursive=True):
        """First node whose label (or class) matches."""
        items = self.objects() if recursive else self.nodes()
        for o in items:
            if cls and o.cls != cls:
                continue
            if label is None or (o.label or "").strip() == label:
                return o
        raise KeyError(f"nothing with label={label!r} cls={cls!r}")

    def cleanup(self):
        self.lv.b.cleanup(self.h)


class VI(Obj):
    def __init__(self, lv, h, diagram, panel, path=None):
        super().__init__(lv, h, "VI")
        self.diagram = Diagram(lv, diagram, "Diagram") if diagram else None
        self.panel = Obj(lv, panel, "Panel")
        self.path = path

    @property
    def label(self):
        return self.path

    def controls(self):
        return [self.lv.obj(h) for h in self.panel.get("Controls[]", "Panel")]

    def ctl(self, label):
        for c in self.controls():
            if c.label == label:
                return c
        raise KeyError(f"no control {label!r}; have {[c.label for c in self.controls()]}")

    def _place(self, style, label, x, y, indicator):
        style = STYLE_ALIASES.get(str(style).lower(), style)
        c = self.lv.obj(self.lv.b.add_object(self.panel.h, style, int(x), int(y)))
        if label:
            c.label = label
        if indicator is not None and isinstance(c, Control) and c.indicator != indicator:
            c.indicator = indicator
        return c

    def control(self, style, label=None, x=50, y=50):
        return self._place(style, label, x, y, False)

    def indicator(self, style, label=None, x=300, y=50):
        if str(style).lower() == "numeric":
            style = "Numeric Indicator (modern)"
        return self._place(style, label, x, y, True)

    def typedef_const(self, ctl_path, where=None, value=None, x=50, y=50):
        """Constant of a typedef (.ctl) on the diagram or inside `where` (a frame Diagram).
        A .ctl can't be dropped on a diagram directly, so: panel instance -> Create Constant -> delete.
        `value` is the whole value, e.g. (False, True, False) for a cluster of 3 booleans."""
        ctl = self.lv.obj(self.lv.b.add_subvi(self.panel.h, os.path.abspath(ctl_path), 0, 0))
        c = ctl.term.constant()
        ctl.delete()
        if where is not None and int(where) != self.diagram.h:
            c.call("Move", cls="GObject", position=(int(x), int(y)), owner=int(where))
        else:
            c.pos = (x, y)
        if value is not None:
            c.set("Value", value, "Constant")
        return c

    # -- connector pane --
    def connector(self, left=(), right=(), pattern=4815):
        """Wire the connector pane. left/right: controls (or labels) top-to-bottom; None skips a slot.
        Default pattern 4815 = 4x2x2x4 (4 terminals on each side)."""
        cp = Obj(self.lv, self.get("Connector Pane:Reference", "VI"), "ConnectorPane")
        if pattern is not None and cp["Pattern"] != pattern:
            cp["Pattern"] = pattern
        b = cp["Terminal Bounds[]"]  # (left, top, right, bottom) per terminal
        w = max(r[2] for r in b)
        cols = {"left": sorted((i for i, r in enumerate(b) if r[0] == 0), key=lambda i: b[i][1]),
                "right": sorted((i for i, r in enumerate(b) if r[2] == w), key=lambda i: b[i][1])}
        for side, items in (("left", left), ("right", right)):
            if len(items) > len(cols[side]):
                raise ValueError(f"pattern {pattern} has only {len(cols[side])} {side} terminals")
            for idx, c in zip(cols[side], items):
                if c is None:
                    continue
                c = self.ctl(c) if isinstance(c, str) else c
                cp.call("Assign Control To Terminal", Control=c.h, TermIdx=idx)
        return cp

    # -- hand-off --
    def todo(self, task, where="diagram", x=20, y=20):
        """Leave a step for the user: a 'USER TODO' free label on the diagram (or panel, or a given
        subdiagram) plus an entry in lv.todos (write it out with lv.flush_todos(folder))."""
        if where == "panel" or not self.diagram:
            owner = self.panel.h
        else:
            owner = where.h if isinstance(where, Obj) else self.diagram.h
        try:
            h = self.lv.b.add_object(owner, "Free Label", int(x), int(y))
        except BridgeError:  # the front panel itself can't own a free label (1060) -> use the diagram
            h = self.lv.b.add_object(self.diagram.h, "Free Label", int(x), int(y))
        lab = self.lv.obj(h, "Text")
        lab.set("Text", "USER TODO: " + task, "Text")
        self.lv.todo(task, os.path.basename(self.path or "untitled"))
        return lab

    def save(self, path=None):
        path = os.path.abspath(path or self.path)
        self.lv.b.save(self.h, path)
        self.path = path
        return path

    def cleanup(self):
        self.diagram.cleanup()


# ---------------- free helpers ----------------
def _term(x):
    """Accept Terminal, Control (-> its diagram terminal), constant/node with a single terminal."""
    if isinstance(x, Terminal):
        return x
    if isinstance(x, Control):
        return x.term
    if isinstance(x, Obj):
        cls = x.cls
        if cls.endswith("Constant") or cls == "Constant":
            return Terminal(x.lv, x.lv.b.constant_terminal(x.h), "Terminal")
        if isinstance(x, Node) and len(x.terms) == 1:
            return x.terms[0]
    raise TypeError(f"can't wire {x!r}")


def wire(a, b):
    a, b = _term(a), _term(b)
    a.lv.b.wire(a.h, b.h)


def set_constant(c, value, rep=None):
    b = c.lv.b
    if isinstance(value, bool):
        b.set_constant(c.h, value, "bool")
    elif isinstance(value, float) or rep in ("dbl", "sgl", "ext"):
        b.set_constant(c.h, value, "dbl")
        if rep and rep != "dbl":
            b.set_representation(c.h, rep)
    elif isinstance(value, int):
        b.set_constant(c.h, value, "i32")
        if rep:
            b.set_representation(c.h, rep)
    else:
        b.set_constant(c.h, value, "string")


def set_node_label(node, text):
    lab = node.wrap(node.get("Label", "Node"), "Text")
    lab.set("Text", text, "Text")
