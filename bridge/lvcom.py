"""Thin, typed wrapper over LabVIEW's ActiveX server (works around pywin32 late-binding issues)."""
import pythoncom
import win32com.client as w

_BYREF = 0x4000


class _Typed:
    """Invoke COM methods using the type library's FUNCDESCs (handles out/optional params)."""

    def __init__(self, disp, iface_name):
        self._o = disp._oleobj_ if hasattr(disp, "_oleobj_") else disp
        ti = self._o.GetTypeInfo()
        tl, _ = ti.GetContainingTypeLib()
        t = None
        for i in range(tl.GetTypeInfoCount()):
            if tl.GetDocumentation(i)[0] == iface_name:
                t = tl.GetTypeInfo(i)
                break
        self._funcs, self._props = {}, {}
        for f in range(t.GetTypeAttr().cFuncs):
            fd = t.GetFuncDesc(f)
            name = t.GetNames(fd.memid)[0]
            if fd.invkind == pythoncom.INVOKE_FUNC:
                self._funcs[name] = fd
            else:
                self._props.setdefault(name, {})[fd.invkind] = fd

    @staticmethod
    def _vt(a):
        t = a[0]
        if isinstance(t, tuple):
            if t[0] == pythoncom.VT_USERDEFINED:  # enums
                return pythoncom.VT_I4
            if t[0] == pythoncom.VT_PTR:
                inner = t[1]
                if isinstance(inner, tuple):
                    inner = pythoncom.VT_I4 if inner[0] == pythoncom.VT_USERDEFINED else inner[0]
                return inner | _BYREF
            return t[0]
        return t

    def call(self, name, *args):
        fd = self._funcs[name]
        types = tuple((self._vt(a), a[1]) for a in fd.args)
        args = list(args) + [pythoncom.Missing] * (len(types) - len(args))
        rt = fd.rettype[0]
        rt = rt[0] if isinstance(rt, tuple) else rt
        return self._o.InvokeTypes(fd.memid, 0, fd.invkind, (rt, 0), types, *args)

    def get(self, name):
        return self._o.Invoke(self._props[name][pythoncom.INVOKE_PROPERTYGET].memid, 0,
                              pythoncom.DISPATCH_PROPERTYGET, True)

    def set(self, name, value):
        self._o.Invoke(self._props[name][pythoncom.INVOKE_PROPERTYPUT].memid, 0,
                       pythoncom.DISPATCH_PROPERTYPUT, False, value)


class LabVIEW:
    def __init__(self):
        self.app = _Typed(w.dynamic.Dispatch("LabVIEW.Application"), "_IApplication")

    def vi(self, path):
        d = self.app.call("GetVIReference", path, "", False, 0)
        return VI(d)


class VI(_Typed):
    def __init__(self, disp):
        super().__init__(disp, "_IVI")

    def get_control(self, name):
        return self.call("GetControlValue", name)

    def set_control(self, name, value):
        self.call("SetControlValue", name, value)
