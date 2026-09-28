"""Who collected the data, and what that turns into on disk.

A shared microscope writes everything into one storage root. Without an owner
recorded at collection time there is nothing in the folder tree that says whose
run a given stack was, and the answer is not recoverable afterwards -- the
server's per-tile ``Workflow.txt`` describes a sweep, not a person.

This module is the whole contract for that name, deliberately free of Qt and of
the configuration service so it can actually be tested (GUI tests in this repo
skip everywhere, so anything that matters must live outside a widget).

Two rules are worth stating outright, because both are load-bearing:

* **No name is a first-class answer.** ``None`` is what the "None" entry in the
  startup dialog produces, and it means *write where we always wrote* -- not a
  folder called "None", not "unknown", not the OS login. A facility that has
  not adopted the convention yet must see byte-identical paths to before.
* **The stored name and the folder name are different strings.** What the
  operator typed is kept verbatim for display and for the acquisition manifest;
  :func:`folder_name_for` is what may touch a filesystem. Sanitising on the way
  in would quietly rewrite someone's name in the UI and in the history list.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

#: Longest folder name we will produce. Well inside every filesystem limit --
#: the constraint that actually bites is total path length on Windows, and this
#: name sits at the top of an already-deep acquisition tree.
MAX_FOLDER_NAME_LENGTH = 48

#: Longest name we will store. Display is not path-bound, so this is generous;
#: it exists to stop a paste accident becoming a permanent history entry.
MAX_STORED_NAME_LENGTH = 120

#: How many previous users the startup dropdown offers, most recent first.
MAX_HISTORY_ENTRIES = 20

# Illegal on Windows, plus the separators, plus anything non-printable. The
# server strips to ASCII 32-126 (SystemSupport::stripUnicode), so a name that
# survives here still has to survive that -- see _to_ascii.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RUN = re.compile(r"\s+")

# Windows refuses these as folder names regardless of extension, and does so
# with an error the operator would see only as a failed acquisition.
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def normalize_stored_name(raw: Optional[str]) -> Optional[str]:
    """Tidy what the operator typed, without changing who they said they are.

    Collapses surrounding and repeated whitespace and caps the length. Returns
    ``None`` for anything empty, which is how a blank box becomes "no user"
    rather than a folder with a blank name.
    """
    if raw is None:
        return None
    collapsed = _WHITESPACE_RUN.sub(" ", str(raw)).strip()
    if not collapsed:
        return None
    return collapsed[:MAX_STORED_NAME_LENGTH]


def _to_ascii(text: str) -> str:
    """Fold accents to ASCII and drop what will not survive the round trip.

    The microscope server strips every byte outside ASCII 32-126 before it
    touches a path, so "Müller" reaching it unfolded becomes "Mller" on disk
    while our side still says "Müller". Folding here means both sides agree.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def folder_name_for(name: Optional[str]) -> Optional[str]:
    """The directory name for a user, or ``None`` when no folder should exist.

    ``None`` in, ``None`` out -- and also ``None`` for any name that sanitises
    away to nothing, because a folder named after a punctuation-only string is
    worse than no folder at all.

    Args:
        name: The stored name, as the operator typed it.

    Returns:
        A folder name safe on Windows and POSIX, or ``None`` for no subfolder.
    """
    stored = normalize_stored_name(name)
    if stored is None:
        return None

    folder = _to_ascii(stored)
    folder = _UNSAFE_CHARS.sub("", folder)
    # Spaces are legal but make every downstream shell command and stitcher
    # path a quoting problem, so they become underscores exactly once.
    folder = _WHITESPACE_RUN.sub("_", folder).strip("_")
    # Windows silently strips trailing dots and spaces from folder names, so a
    # name ending in one would not round-trip through its own path.
    folder = folder.strip(". ")
    folder = folder[:MAX_FOLDER_NAME_LENGTH].strip("_. ")

    if not folder:
        return None
    if folder.upper() in _RESERVED_NAMES:
        # Suffixing beats rejecting: the operator gets their run, and the name
        # is still recognisably theirs.
        folder = f"{folder}_user"
    return folder


def describe_folder_effect(name: Optional[str]) -> str:
    """One line for the dialog saying what this choice does to the path.

    The startup prompt is the only place the operator sees this decision, so it
    has to state the consequence rather than restate the input.
    """
    folder = folder_name_for(name)
    if folder is None:
        return "No user folder -- data is saved exactly as it was before."
    stored = normalize_stored_name(name)
    if stored is not None and folder != stored:
        return f"Data is saved under a top-level folder: {folder}/  (from “{stored}”)"
    return f"Data is saved under a top-level folder: {folder}/"


def merge_into_history(
    history: Optional[Iterable[str]], name: Optional[str]
) -> List[str]:
    """Put ``name`` at the front of the remembered list, without duplicating it.

    Matching is case-insensitive on the *folder* name, so "Sam Nelson" and
    "sam nelson" do not become two entries pointing at one directory. The
    spelling kept is the one just used, because that is the one the operator
    most recently chose to type.

    ``None`` leaves the list untouched: declining to give a name is not an
    event worth remembering.
    """
    existing = [n for n in (normalize_stored_name(h) for h in (history or [])) if n]
    stored = normalize_stored_name(name)
    if stored is None:
        return existing[:MAX_HISTORY_ENTRIES]

    key = (folder_name_for(stored) or stored).casefold()
    kept = [n for n in existing if (folder_name_for(n) or n).casefold() != key]
    return [stored, *kept][:MAX_HISTORY_ENTRIES]
