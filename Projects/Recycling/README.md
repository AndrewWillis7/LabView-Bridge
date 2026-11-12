# Recycling practice test (ENGR 3350)

This is the next benchmark for the bridge. The start files (`Test Start Folder.zip`: `Recycling.lvproj`,
`Recycle.vi`, `controls/State.ctl`, `controls/Data.ctl`, `controls/actions.ctl`) are **not here yet**,
so the whole program is built from scratch with its own typedefs. It passes the test's expected-behavior
check headless (no mouse or keyboard).

## What's here
- `spec/page_photo_*.webp`: phone photos of the test pages.
- `build_barcode.py`: builds `Barcode.vi` (step 2 of the test) and runs it on all five barcode cases.
  Until the real start files arrive, it creates a stand-in `controls/actions.ctl` (a cluster of three LEDs:
  Invalid, Bottle, Can).

- `build_recycle.py`: builds `Recycle.vi` (steps 1 and 3–7) in about 6 s:
  - its own typedefs: `controls/Recycle State.ctl` and `controls/Recycle Data.ctl` (Reset, Delay Time (s),
    Total ($), Actions);
  - front panel: combo box, latched Sort/Receipt/Exit buttons, Current Total and Actions lights;
  - all five states;
  - file I/O: open before the loop, header in Initialize, one line per Receipt, close and Simple Error
    Handler after the loop.
- `check_recycle.py`: runs the test's 5-step check headless. It presses buttons through ActiveX and closes
  the receipt popup by posting Enter to its window.
- `TODO_USER.md`: what's left for you (icons).

```bash
python Projects/Recycling/build_barcode.py
python Projects/Recycling/build_recycle.py
python Projects/Recycling/check_recycle.py
```

When the real start files arrive, the same steps apply to the template's `Recycle.vi` and typedefs. The
script then opens those files instead of creating its own.

## The task (transcribed)
An automated bottle/can recycling machine reads a barcode from a combo box. It lights one of three lights,
adds the item's value to a running total, and writes a receipt line to a file.

| Barcode | Item | Light | Value | Delay time |
|---|---|---|---|---|
| `2L B` | 2 liter bottle | Bottle | $0.10 | 2 s |
| `1L B` | 1 liter bottle | Bottle | $0.05 | 2 s |
| `500mL B` | 500 ml bottle | Bottle | $0.05 | 2 s |
| `16oz C` | 16 oz can | Can | $0.05 | 2 s |
| anything else (e.g. `tin can`) | invalid item | Invalid | $0.00 | 1 s |

| State | What it does | Next state |
|---|---|---|
| Initialize (optional) | Open the data file with low-level File I/O; optional header line | Read |
| Read | Call the barcode subVI; check Exit, then Receipt, then Sort; Wait ~100 ms | Sort / Receipt / Exit / Read |
| Sort | Keep the item's light on until Elapsed Time (Express VI) passes Delay Time; then all lights off | Read when expired, else Sort |
| Receipt | One Button Dialog "Your total is $0.35" (`%.2f`); write date, time and total to the file; Total = 0 | Read |
| Exit | Close the file, Simple Error Handler, stop the loop | (stop) |

### Steps from the test
1. **Typedefs.** State.ctl items: Read, Sort, Receipt (keep Initialize and Exit). Data.ctl: Reset (Boolean),
   Delay Time (DBL, s), Total (DBL, $), Actions (actions.ctl inside the cluster).
2. **Barcode subVI.** Barcode (string) in. Actions, Delay Time and Value out. A string case per barcode,
   with Default = invalid. Connector pane: Barcode on the left; Actions, Delay Time and Value on the right.
   Label everything and draw an icon.
3. **Read state.** Sort, Receipt and Exit buttons set to Latch When Released, with terminals read directly
   (no locals). Call the subVI every pass. Sort adds Value to Total and stores Actions, Delay Time and
   Reset = TRUE, then goes to Sort. Show Total on a Current Total indicator. Update the cluster with
   Bundle by Name.
4. **Sort state.** Elapsed Time Express VI with Time Target = Delay Time and Reset from the cluster, then
   store Reset = FALSE. Show Actions on the panel lights. When Time has Elapsed is TRUE, set all lights
   FALSE and go to Read. Otherwise stay in Sort with a short wait (~10 ms, not Wait = delay).
5. **Receipt state.** Error chain: One Button Dialog, then Get Date/Time String, then Concatenate Strings
   (date, Tab, time, Tab, total as `%.2f`, End of Line), then Write to Text File on the file refnum from the
   shift register. Total = 0 via Bundle by Name.
6. **File I/O.** Open/Create/Replace (replace or create) once, with a path constant, before the loop or in
   Initialize. Pass the refnum through every case (no Use Default If Unwired). Close it in Exit or after the
   loop.
7. **Exit.** Only Exit makes the loop stop. Close File, then Simple Error Handler.

### Expected behavior (the test's check)
1. `2L B` + Sort: Bottle light on for 2 s; total $0.10.
2. `16oz C` + Sort: Can light on for 2 s; total $0.15.
3. `tin can` + Sort: Invalid light on for 1 s; total stays $0.15.
4. Receipt: popup shows $0.15, one date/time/total line is added to the file, and the total resets to $0.00.
5. Exit: the file closes and the program stops.

## Bridge coverage (see the root README)
Every step maps to tested bridge helpers except the icon (left as a `vi.todo`) and closing the receipt
popup during an automated run-check.
