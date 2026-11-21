"""The workflow exposure key, in the form the connected server reads.

The app keeps the exposure in microseconds under ``Exposure time (us)``, in its
templates, its workflow builders and the files it saves. No server reads that
key. The server source:

* 3.0.0 reads ``Exposure time (ns)`` (``WorkflowSettings.h``), whose value is
  the exposure in **milliseconds** despite the label;
* builds from 2026-10 read ``Exposure time (ms)``, and still accept the old
  ``(ns)`` label (``WFSettings`` ``attributeFound``, ``WF_EXPOSURE_TIME_OLD_LABEL``).

Both pass the value to ``CAMERA_EXPOSURE_SET`` as an integer number of
milliseconds (``WorkflowBase.cpp``). So a workflow the app sent ran with
whatever exposure the camera had before; the verification run on CTLSM1
(2026-11-21) asked 28 ms and the camera stayed at 25 ms.

:func:`to_server_exposure` rewrites every ``Exposure time (us)`` line of a
workflow text into the server's key with the value in whole milliseconds. It
is applied once, where workflow text is sent (``MicroscopeCommandService``),
so templates, generated workflows and files loaded from disk are all covered,
and the app's own files keep the microsecond form its readers expect.
"""

from __future__ import annotations

import re
from typing import Optional, Union

US_KEY = "Exposure time (us)"
MS_KEY = "Exposure time (ms)"
# The 3.0.0 label. Its value is milliseconds. Every build known so far accepts it.
OLD_KEY = "Exposure time (ns)"

_LINE = re.compile(r"^(?P<indent>\s*)Exposure time \(us\)\s*=\s*(?P<value>.*?)\s*$")


def server_exposure_key(layout=None) -> str:
    """The key the server with this command layout reads.

    3.0.0 (the reference layout) reads only the old label; later builds read
    ``(ms)``. With no layout identified, the old label, which every build
    known so far accepts."""
    from py2flamingo.core import server_layout

    if layout is None:
        layout = server_layout.active()
    if layout is None or layout is server_layout.REFERENCE:
        return OLD_KEY
    return MS_KEY


def us_to_ms_text(value: str) -> str:
    """'24998' or '9,002' (microseconds) as whole milliseconds; blank stays blank."""
    raw = value.replace(",", "").strip()
    if not raw:
        return ""
    try:
        us = float(raw)
    except ValueError:
        return value
    if us <= 0:
        return "0"
    return str(max(1, int(round(us / 1000.0))))


def to_server_exposure(
    text: Union[bytes, str], layout=None, key: Optional[str] = None
) -> Union[bytes, str]:
    """``text`` with each ``Exposure time (us) = N`` line rewritten to the
    server's key and N in milliseconds. Lines already in ``(ms)`` or ``(ns)``
    are left alone. Returns the same type it was given."""
    as_bytes = isinstance(text, (bytes, bytearray))
    s = bytes(text).decode("utf-8", errors="replace") if as_bytes else text
    if US_KEY not in s:
        return text
    target = key or server_exposure_key(layout)
    out = []
    for line in s.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        m = _LINE.match(body)
        if m:
            body = f"{m.group('indent')}{target} = {us_to_ms_text(m.group('value'))}"
        out.append(body + ending)
    result = "".join(out)
    return result.encode("utf-8") if as_bytes else result
