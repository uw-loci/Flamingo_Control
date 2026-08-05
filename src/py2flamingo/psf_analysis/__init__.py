"""psf_analysis — measure optical resolution (FWHM) from 3-D bead images.

Self-contained package (numpy / scipy / scikit-image only; no other py2flamingo
imports, no Qt) so it is independently testable, CLI-runnable
(``python -m py2flamingo.psf_analysis``), and extractable to a standalone repo.

Public API::

    from py2flamingo.psf_analysis import (
        PSFAnalysisService, PSFSettings, PSFResult, PSFBead, AxisFit, load_volume,
    )

Credit: the analysis reimplements the approach of mesoSPIM-PSFanalysis
(https://github.com/mesoSPIM/mesoSPIM-PSFanalysis, MIT), itself derived from Nick
Sofroniew's ``psf`` (https://github.com/sofroniewn/psf, MIT). See ``NOTICE``.
"""

from py2flamingo.psf_analysis.io import load_volume
from py2flamingo.psf_analysis.models import AxisFit, PSFBead, PSFResult
from py2flamingo.psf_analysis.service import PSFAnalysisService, PSFSettings

__all__ = [
    "PSFAnalysisService",
    "PSFSettings",
    "PSFResult",
    "PSFBead",
    "AxisFit",
    "load_volume",
]
