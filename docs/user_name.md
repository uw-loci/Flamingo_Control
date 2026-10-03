# User name

When Py2Flamingo starts it asks who is using the microscope. This page is what
the **"What does the user name change?"** link in that dialog points at, and it
is the list to update whenever the name starts affecting something new.

## What it changes today

**One thing: the top level of the acquisition folder tree.**

Without a user name (or with **None** chosen), data lands where it always did:

```
D:/CTLSM1/BrainSingleChannel2/2026-09-28/X4.47_Y17.17/
```

With a user name, that same run lands one level deeper:

```
D:/CTLSM1/sam_nelson/BrainSingleChannel2/2026-09-28/X4.47_Y17.17/
```

The `AcquisitionManifest.txt` for the run moves with it, into the same date
folder as the tiles.

## What it does *not* change

- **Nothing on the microscope's own disk during a run.** While an acquisition is
  in progress the server writes its flat, timestamped folders exactly as before.
  The user folder is created on this PC when those folders are moved into the
  nested layout after the run.
- **No file contents.** No raw file, MIP, `Workflow.txt` or `ScopeSettings.txt`
  changes in any way.
- **No microscope settings.** It is not sent to the scope and does not appear in
  any command.
- **Nothing about who may do what.** This is filing, not access control. Anyone
  can pick any name, and picking one grants and restricts nothing.

## Why it only affects the local copy

The microscope server creates the experiment directory with a single
non-recursive `mkdir` (`SystemSupport::makeDirectory`). It can make
`<drive>/<timestamp>_<name>` because `<drive>` already exists, but it cannot
make a folder inside a directory that does not exist yet — that call fails, and
a failed experiment directory aborts the workflow rather than falling back.

So the user folder is added where the nested layout is already built: on this
PC, when tile folders are reorganized after collection.

**This means the user folder requires local access to the save drive**, the same
setting the nested `base/date/tile` layout has always required. With local
access off, the data stays flat on the server and no user folder appears.

## Choosing a name

- Pick yourself from the dropdown, type a new name, or choose **None**.
- The list remembers everyone who has used this PC, most recent first, and
  starts on whoever went last.
- The name is stored as you typed it. The *folder* is a safe version of it:
  accents are folded (`Müller` → `Muller`), spaces become underscores, and
  characters illegal in a path are dropped. The dialog shows the resulting
  folder before you accept.
- **None is a real answer**, not a skipped one. It means "save exactly as
  before", and it is remembered like any other choice.

## Where it is stored

`session_paths.json`, next to the application, under `user_name` and
`user_name_history`. It is per-PC, not per-user-account, and it is not sent
anywhere.

## For developers

| Concern | Location |
|---|---|
| Name rules (sanitizing, history, None) | `src/py2flamingo/models/user_name.py` |
| Startup dialog | `src/py2flamingo/views/dialogs/user_name_dialog.py` |
| Prompt at launch | `FlamingoApplication.prompt_for_user_name` |
| Storage | `ConfigurationService.get_user_name` / `set_user_name` |
| Folder path | `utils/tile_folder_organizer.acquisition_root` |

`acquisition_root()` is the single place the acquisition folder path is built.
If the user name should affect a new path, call that helper rather than
composing the path again, and add the behaviour to the list above.
