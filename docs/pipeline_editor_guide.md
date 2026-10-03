# Pipeline Editor guide

A pipeline is a chain of steps the microscope runs for you: look at an image,
decide something, act on it. Detect objects in an overview, then re-image each
one at higher resolution. You build it by dragging boxes onto a canvas and
joining them with wires, without writing JSON or code.

This guide walks the whole screen in the order you meet it. If you would rather
author pipelines from a script or run them without a microscope, see
[Headless pipelines](headless_pipelines.md); for the file format itself, see
[the JSON format reference](pipeline_json_format.md).

> **A pipeline moves the stage and acquires data.** Read a pipeline before you
> run it, especially one someone sent you. Click each node and check its
> settings against your sample.

## Opening it

**Open `Extensions > Pipeline Editor...`.** A window titled **Pipeline Editor**
appears, split into five areas:

| Area | Where | What it is |
|---|---|---|
| Toolbar | across the top | New, Open, Save, Validate, Run, Stop, and a status word |
| **Node Types** | left | the palette you drag from |
| Canvas | center | where the pipeline lives |
| **Properties** | right | settings for whichever node is selected |
| Log | along the bottom | what happened, newest last |

The canvas starts empty, reading *"Drag node types from the palette onto this
canvas to build a pipeline"*, and the Properties panel reads *"Select a node on
the canvas to edit its properties"*.

## Building a pipeline

The example below thresholds the volume currently loaded in the 3-D viewer,
using two nodes.

1. **Drag `Sample View Data` from the Node Types list onto the canvas.** A node
   appears where you dropped it, and the log reads `Added SAMPLE_VIEW_DATA node`.

   The palette shows friendly names, "Sample View Data" and "Post Processing",
   where the log and the JSON use the underlying `SAMPLE_VIEW_DATA`. Search the
   reference docs by the underlying name, not the palette spelling.

2. **Drag `Threshold` onto the canvas, to the right of the first node.**

3. **Join them.** Drag from the `volume` dot on the right edge of Sample View
   Data to the `volume` dot on the left edge of Threshold. While you drag, the
   wire turns green when it is over a port that will accept it. Release there
   and the wire stays.

   Press **Esc** mid-drag to abandon a wire.

4. **Click the Threshold node** and set its thresholds in the Properties panel
   on the right. The fields are labeled but not explained on screen. What each
   one means, its units and its sane range are in
   [the JSON format reference](pipeline_json_format.md#threshold), which lists
   every setting beside the label the panel shows.

5. **Click Validate.** A dialog titled **Valid** reads *"Pipeline is valid and
   ready to run."* If instead you get **Validation Failed**, it lists what is
   wrong. See [When Validate complains](#when-validate-complains).

6. **Click Run.** The status word turns blue and reads *Running...*, each node
   shows a colored dot as it goes, and the log fills in. When it finishes the
   status returns to *Ready*.

**Save early.** The editor has **no unsaved-changes warning**: clicking New, or
closing the window, discards your pipeline without asking. Click **Save**, pick
a location you will find again, and save often.

## Reading the canvas

**Ports** are the dots on each node's edges: inputs on the left, outputs on the
right, each labeled with its name. Hover one to see its direction, its name,
its data type, and whether it is required.

A port only accepts certain types. A `VOLUME` output cannot feed an
`OBJECT_LIST` input, and the editor will refuse the wire **silently**, without
saying why. The full table of what may connect to what is in
[the JSON format reference](pipeline_json_format.md#port-type-compatibility).
The commonest mistakes:

- `objects` (a list) goes into a For Each node's `collection`, not into a
  `volume` input.
- `current_item` (one object) comes **out** of For Each, and that is what you
  feed to a Workflow node.
- A `trigger` port carries no data. It only says "run this after that".

**Each node type has its own header color**, so two Threshold nodes look alike
and a Threshold and a Workflow do not. The color carries no other meaning.
**The dot in a node's header** is its state during a run:

| Dot | Means |
|---|---|
| Gray | not started |
| Blue | running now |
| Green | finished |
| Red | failed, read the log |

## Moving around

Nothing on screen says this, so:

- **Scroll** to zoom (down to a fifth, up to three times).
- **Right-drag** to pan.
- **Right-click** an empty part of the canvas for a menu with **Add Node**,
  **Fit to Content** and **Reset Zoom**. A right-click that moves even slightly
  pans instead of opening the menu, so keep still.

## When Validate complains

Validate checks four things. Three of its messages name the node and port, and
one does not:

| Message | What to do |
|---|---|
| `Required input 'X' on 'Y' is not connected` | Wire something into that port on that node. A Post Processing node's `acquisition_dir` is required. |
| `Type mismatch on connection <id>: A -> B` | Two incompatible ports are joined. The id is not shown on the canvas, so find the wire by the two type names. |
| `Pipeline contains a cycle` | A wire leads back to a node it came from. Follow the wires round and remove one. |
| `Connection <id> references missing node/port` | A node was deleted leaving a wire behind. Re-save, or delete and redraw that wire. |

Run validates first too, and refuses with a **Cannot Run** dialog if anything
is wrong. Using Validate early saves you the round trip.

## After a run

The log is the only report you get, so read it before trusting the result:

```
Running: Threshold
ForEach Re-image each: iteration 3/40
Pipeline completed successfully
```

What it will and will not tell you:

- **The log pane is short and does not scroll itself.** Scroll back if a run
  produced more than a few lines.
- **The status word says *Ready* whether the run succeeded or failed.** Only a
  red dot and the log distinguish them, so check both.
- **Nothing tells you where output files went.** A Post Processing node writes
  to the folder in its `output_dir` setting; note it before you run, because
  the editor will not repeat it afterwards.

**Stop** cancels between nodes, not inside one. A node already running finishes
first, and a Python Function node stuck in a loop cannot be interrupted at all.

## Where files go

**Save puts the pipeline wherever you choose.** The dialog opens in whatever
folder the application was started from, so navigate deliberately the first
time.

Note that `py2flamingo-pipeline list` on the command line reads
`~/.flamingo/pipelines/` instead, and does not see pipelines you saved from the
editor somewhere else. If you want both to find the same files, save into
`~/.flamingo/pipelines/`.

## What this guide does not cover

- **Writing your own analysis step** in Python. See
  [the Python Function node](pipeline_python_function_node.md).
- **Every setting on every node type.** See
  [the JSON format reference](pipeline_json_format.md), which lists each node's
  ports and settings with their units and defaults.
- **Running without the GUI.** See [Headless pipelines](headless_pipelines.md).
- **How the system is built**, for contributors. See
  `claude-reports/design/pipeline-system.md`.
