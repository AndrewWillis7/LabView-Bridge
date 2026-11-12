"""Headless run-check of Recycle.vi: the 5-step 'expected behavior' from the test, without mouse/keyboard.

Buttons are pressed with ActiveX SetControlValue (works for latched booleans). The Receipt popup is a
modal dialog, so it's closed by posting a message to its window (no focus change, no input injection).
"""
import sys
import threading
import time
from pathlib import Path

import win32con
import win32gui
import win32process

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bridge"))
from lvcom import LabVIEW  # noqa: E402

DATA = HERE / "recycling data.txt"
vi = LabVIEW().vi(str(HERE / "Recycle.vi"))
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def press(btn):
    vi.set_control(btn, True)


def lights():
    return tuple(bool(x) for x in vi.get_control("Actions"))


def sort_item(code, light, total, hold):
    vi.set_control("Barcode", code)
    time.sleep(0.3)
    press("Sort")
    time.sleep(0.6)
    on = lights()
    time.sleep(hold + 0.2)
    off = lights()
    time.sleep(0.3)
    t = round(vi.get_control("Current Total ($)"), 2)
    check(f"{code}: {light} light on", on == light, f"lights={on}")
    check(f"{code}: lights off after {hold:g} s", off == (False, False, False), f"lights={off}")
    check(f"{code}: total ${total:.2f}", abs(t - total) < 1e-9, f"total={t}")


def close_dialog(timeout=5.0):
    """Find LabVIEW's modal dialog and press its button via messages; returns the dialog text."""
    pid = win32process.GetWindowThreadProcessId(win32gui.FindWindow("LVFrame", None))[1]
    end = time.time() + timeout
    while time.time() < end:
        found = []

        def cb(h, _):
            # LabVIEW dialogs are untitled LVDChild windows (front panels and diagrams have titles)
            if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == pid \
                    and win32gui.GetClassName(h) == "LVDChild" and not win32gui.GetWindowText(h):
                found.append(h)
        win32gui.EnumWindows(cb, None)
        if found:
            h = found[0]
            text = win32gui.GetWindowText(h)
            win32gui.PostMessage(h, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
            win32gui.PostMessage(h, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
            time.sleep(0.5)
            if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h):
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            return text or "LabVIEW dialog"
        time.sleep(0.1)
    return None


vi.call("Run", True)
time.sleep(1.0)
sort_item("2L B", (False, True, False), 0.10, 2.0)
sort_item("16oz C", (False, False, True), 0.15, 2.0)
sort_item("tin can", (True, False, False), 0.15, 1.0)

press("Receipt")
dlg = close_dialog()
check("Receipt: popup appeared and was closed", dlg is not None, f"window={dlg!r}")
time.sleep(0.6)
check("Receipt: total reset to $0.00", vi.get_control("Current Total ($)") == 0.0)

press("Exit")
time.sleep(1.0)
check("Exit: VI stopped", vi.get("ExecState") == 1, f"ExecState={vi.get('ExecState')}")
lines = DATA.read_text().splitlines() if DATA.exists() else []
print("data file:", lines)
check("file: header + one receipt line ending in 0.15", len(lines) == 2 and lines[1].endswith("\t0.15"))
print("ALL PASS" if all(results) else f"{results.count(False)} FAILED")
