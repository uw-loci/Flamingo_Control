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

That is the default a new node starts with, and it runs as-is.

## What's available

**The libraries are provided, never imported.** `import` is rejected — there is
nothing to import, and nothing else to reach:

| Name | Is |
|---|---|
| `np` | numpy |
| `ndi` | scipy.ndimage |
| `filters`, `measure`, `morphology`, `exposure` | scikit-image submodules |
| `math` | math |
| `log(...)` | writes to the run log |

**Inputs** are the node's input ports, by name: `volume`, `value`, `objects`,
and `params` (the node's own config dict). An unconnected input is `None`.

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
  node runs in the calling thread.

Both are addressed by the planned change below.

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
