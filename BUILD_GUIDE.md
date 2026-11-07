# Build `LVBridge Server.vi` (one-time, ~30 min)

This is the only piece built by hand. It's a small loop that runs inside LabVIEW and executes
VI Scripting commands that Python drops into its front panel. Everything else is generated.

Save it as: **`Documents\LabVIEW-Bridge\labview\LVBridge Server.vi`**

> Tip: open `examples\Application Control\VI Scripting\Creating VIs\Creating New VI From Scratch.vi`
> (Help » Find Examples). Its diagram already contains configured **New VI Object**, **Terms[]**,
> and **Connect Wire** nodes — you can copy/paste them.

---

## 0. Two reusable tricks (used in almost every case)

**Ref → U32** (to send a reference back to Python):
`Type Cast` (Numeric » Data Manipulation) with **x** = the reference, **type** = a numeric constant set to **U32**
(right-click constant » Representation » U32). Make one U32 constant and wire it to all of these.

**U32 → typed ref** (to use a handle Python sent):
1. Drop the Property/Invoke Node you need and pick its class (right-click » Select Class » VI Server » …).
2. Right-click its **reference** input » Create » Constant → this is a refnum constant of exactly that class.
3. Insert a `Type Cast`: **x** = the U32 handle, **type** = that refnum constant, output → node's reference input.

---

## 1. Front panel (labels must match exactly — Python uses them)

| Label     | Kind      | Type                       |
|-----------|-----------|----------------------------|
| `cmd`     | Control   | String                     |
| `seq`     | Control   | I32 numeric                |
| `h`       | Control   | 1D Array of **U32** numeric |
| `s`       | Control   | 1D Array of String         |
| `n`       | Control   | 1D Array of **I32** numeric |
| `stop`    | Control   | Boolean                    |
| `ack`     | Indicator | I32 numeric                |
| `out_h`   | Indicator | 1D Array of U32            |
| `out_s`   | Indicator | 1D Array of String         |
| `out_n`   | Indicator | 1D Array of I32            |
| `err`     | Indicator | String                     |
| `style_ring` | Control | (created in step 4, case `new_obj`) |

## 2. Main loop skeleton

1. **While Loop**. Wire `stop` → loop condition (Stop if True). Put **Wait (ms)** = `5` inside.
2. Add a **Shift Register**. Initialize the left side with constant **-1**. Wire `seq` → right side every iteration.
3. `Not Equal?` (`seq`, left shift register) → selector of an outer **Case Structure**.
   **False** case: nothing. **True** case: continue below.
4. In the True case, add a **Flat Sequence** with 2 frames.
   - **Frame 2**: wire `seq` → `ack` indicator (put the `ack` terminal here).
   - **Frame 1**: everything else (steps 3–5).

## 3. Inputs (Frame 1, left side)

- `Index Array` on `h`, stretched to 2 outputs → **h0**, **h1**
- `Index Array` on `n`, stretched to 3 outputs → **n0**, **n1**, **n2**
- `Index Array` on `s`, 1 output → **s0**
- Keep the whole `s` array available too.

Add a **Case Structure** whose selector is `cmd` (string cases). Wire h0, h1, n0, n1, n2, s0, s into it.
Every case outputs up to 4 things through tunnels on the right: **out_h** (U32 array), **out_s**,
**out_n**, **error**. Right-click each output tunnel » **Use Default If Unwired**.

## 4. The cases

Case names are exact strings (no quotes).

**`new_vi`**
`New VI` (Application Control » VI Scripting) → **Property Node** (VI class) with two read items:
`Block Diagram` and `Front Panel`.
Type Cast the VI ref, the Block Diagram ref, and the Front Panel ref to U32 → `Build Array` (3 inputs,
in that order) → out_h. Error out → error tunnel.

**`open_vi`**
`String To Path`(s0) → `Open VI Reference` → same Property Node + 3 Type Casts + Build Array as `new_vi`
(copy/paste them).

**`new_obj`**
`New VI Object` (VI Scripting palette):
- **vi object class**: right-click » Create » Constant, then right-click the constant » Select VI Server Class »
  Generic » **GObject**.
- **owner reference**: Type Cast(h0, type = refnum constant from right-click owner reference » Create » Constant).
- **style**: wire **n0** (coercion dot is fine).
  Then also right-click **style** » Create » **Control** → rename that new panel control **`style_ring`**,
  delete the wire it made, and move its diagram terminal into the `styles` case (below).
- **position/next to**: right-click » Create » Constant (x,y cluster). Feed that constant into the middle of a
  `Bundle` and wire **n1** → x, **n2** → y; Bundle output → position/next to.
- object reference → Type Cast U32 → Build Array (1 input) → out_h. Error → tunnel.

**`new_subvi`**
Right-click the `new_obj` case » **Duplicate Case**, rename `new_subvi`, and additionally wire
`String To Path`(s0) → **path** input of New VI Object.

**`terms`**
Type Cast(h0 → **Node** refnum) → Property Node (class Node) read `Terms[]` →
**For Loop** (auto-index the array in) → Type Cast each element to U32 → auto-indexed output → out_h.

**`wire`**
Invoke Node, class **Terminal**, method `Connect Wire`.
reference = Type Cast(h0 → Terminal refnum); **Wire Source** = Type Cast(h1 → Terminal refnum, reuse the constant).

**`ctl_term`**
Type Cast(h0 → **Control** refnum) → Property Node read `Terminal` → Type Cast U32 → Build Array → out_h.

**`set_props`**
Invoke Node, class **Property** (the Property Node class: Generic » GObject » Node » … » Property),
method `Set Properties[]` (Properties » Set). reference = Type Cast(h0 → that class's refnum).
**ID Strings[]** = the whole `s` array. (Leave other inputs default.)

**`set_const`**
Type Cast(h0 → **Constant** refnum) → Property Node, item `Value`, right-click » Change To Write.
Feed it from an inner Case Structure on **n0** (numeric cases):
- `0, Default`: `To Variant`(s0)
- `1`: `To Variant`(`Fract/Exp String To Number`(s0))   ← DBL
- `2`: `To Variant`(n1)
- `3`: `To Variant`(`Not Equal To 0?`(n1))

**`save`**
Invoke Node, class **VI**, method `Save Instrument` (Save » Instrument). reference = Type Cast(h0 → VI refnum).
**Path** = `String To Path`(s0).

**`cleanup`**
Invoke Node, class **Diagram**, method `Clean Up Diagram`. reference = Type Cast(h0 → Diagram refnum).

**`close`**
`Close Reference` with reference = Type Cast(h0 → GObject refnum).

**`styles`**
Put the `style_ring` terminal here. Right-click it » Create » Property Node » `Strings And Values[]`
(if it only offers `Strings[]`, the style list is an enum — use `Strings[]`, wire it to out_s, and skip out_n).
For Loop over the array → `Unbundle` → String → out_s (auto-index), Value → `To Long Integer` → out_n (auto-index).

**`Default`** (make the *Default* case)
Error cluster constant with status = **T**, code = **5000**, source = `LVBridge: unknown command` → error tunnel.

## 5. Outputs (Frame 1, right of the cmd case)

- out_h tunnel → `out_h` indicator; out_s → `out_s`; out_n → `out_n`.
- error tunnel → `Simple Error Handler.vi` (Dialog & UI) with **type of dialog** = constant `no dialog`.
  Its **message** output → `err` indicator.

## 6. Save and tell Claude

Save as `Documents\LabVIEW-Bridge\labview\LVBridge Server.vi`. You don't need to run it —
the Python side starts it automatically. If anything in this guide doesn't match what you see in
LabVIEW 2026 (a renamed method, a missing property), skip that case and tell Claude; the core cases
are `new_vi`, `new_obj`, `terms`, `wire`, `save`.
