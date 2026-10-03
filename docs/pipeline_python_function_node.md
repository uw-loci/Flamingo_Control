# Pipeline: Python Function node

A node you fill in yourself, for the analyses the built-in nodes don't cover —
a thresholding rule nobody anticipated, a "is this tile worth imaging" test, a
scalar derived from a volume in whatever way this week's science needs.

Drag **Python Function** from the node palette.

## What you write

The body of a function. `return` works as it looks:

```python
threshold = filters.threshold_otsu(volume)
mask = volume > threshold
log("threshold", threshold, "-> voxels", int(mask.sum()))

return {
    "mask": mask,
    "value": float(threshold),
    "boolean": bool(mask.sum() > 0),
}
```

That is the default a new node starts with. It needs a volume on the
`volume` input port: unlike the Threshold node, Python Function does **not**
fall back to the current view or to `--input` on its own, so wire a Sample
View Data or Workflow node into it first. With nothing connected `volume` is
`None` and the body fails with `Either image or hist must be provided.`

## What's available

**The libraries are provided, never imported.** `import` is rejected, so these
are what a body has by name:

| Name | Is |
|---|---|
| `np` | numpy |
| `ndi` | scipy.ndimage |
| `filters`, `measure`, `morphology`, `exposure` | scikit-image submodules |
| `math` | math |
| `log(...)` | writes to the run log, capped at 200 lines per run |

**Builtins are a short allowlist**, not Python's usual set: `abs any all bool
dict divmod enumerate filter float int isinstance len list map max min pow range
reversed round set slice sorted str sum tuple zip`, plus `ValueError TypeError
RuntimeError Exception`. Anything else is absent — **including `print`; use
`log()`**. A missing builtin fails when the pipeline runs, not while you type,
so the editor will say "Code is valid." first. A further set is rejected
outright when you save (`eval`, `exec`, `open`, `getattr`, `setattr`, `type`,
`dir`, `vars`, `globals`, `locals`, `super`, `object`, `compile`, `input`), as
are `global`, `nonlocal`, and any dunder name or attribute.

**Inputs** are the node's input ports, by name:

| Name | Is |
|---|---|
| `volume` | one 3-D `(Z, Y, X)` array |
| `volumes` | every channel, as `{channel_id: array}` |
| `value` | whatever is wired to the `value` port |
| `objects` | whatever is wired to the `objects` port |
| `params` | the node's own config dict |

An unconnected input is `None` (and `volumes` is `{}`).

**Why both `volume` and `volumes`:** a VOLUME port can carry either a single
array or a channel dict depending on what is upstream — a Sample View Data node
emits the dict, a single-channel `--input` emits an array. Rather than make
every body guess, the node normalizes: `volume` is always one array (the lowest
channel id when a dict arrives), and `volumes` is always the full mapping for
when you need the other channels.

## What you return

Return a dict with any of these keys, each feeding the output port of the same
name:

| Key | Port type | Use |
|---|---|---|
| `value` | SCALAR | a number |
| `boolean` | BOOLEAN | true/false, **and it picks the branch** |
| `mask` | VOLUME | an array |
| `objects` | OBJECT_LIST | detected objects |
| `result` | ANY | anything else |

A misspelled key is an error, not a silent drop.

You can also return a bare value, which becomes `result`. A bare boolean — and
that includes a numpy comparison like `volume.max() > 5` — also sets `boolean`
and selects a branch, so a true/false step is a one-liner:

```python
return volume.max() > 500
```

**Branching is optional.** A body that returns no boolean is an analysis step,
not a decision, and neither branch fires.

## Errors

The editor validates as you type and reports the line in *your* text. Rejected
code never runs — a bad node fails when you write it, not partway through an
acquisition.

## The honest limits

The checks are a guard against mistakes, **not a security boundary.** Python
cannot be made safe against hostile code in-process, and this does not try. Two
things follow:

- **Treat a pipeline file like a script, not like data.** Opening someone
  else's pipeline means running their Python. Read it first.
- **A loop has no timeout.** `while True` hangs the acquisition, because the
  node runs in the calling thread. **Stop will not break out of it** — the
  executor only checks for cancellation between nodes
  (`engine/executor.py:94,145`), so you will have to kill the application.

Both are on the roadmap; see the planned change below.

## TODO: Appose-powered execution

Planned: move execution to an [Appose](https://github.com/apposed/appose)
worker. Two reasons, in order of when they'll bite:

1. **Speed in loops.** Every invocation inside a ForEach or Timed Loop
   currently hands the body a fresh copy of the volume. Appose's shared-memory
   handoff removes the per-iteration copy, which is the first thing that will
   hurt on real stacks.
2. **Containment.** A supervised worker can be killed on a timeout, so a
   runaway body fails the node instead of the run, and a segfault in a C
   extension doesn't take the application down.

The seam is in place: `run_code()` in
`pipeline/models/python_function.py` is the only entry point and
`PythonFunctionRunner` calls nothing else, so the backend can change without
touching node definitions, ports, or saved pipeline files. Nearest precedent in
this repo is the isolated-venv subprocess pattern used for Leonardo and
flat-field (`isolated_service.py` / `isolated_worker.py`).
