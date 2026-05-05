"""STEP file loading via OpenCascade."""

from __future__ import annotations

import os
from typing import Optional, Callable

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib


class StepLoadError(RuntimeError):
    pass


def load_step(path: str, progress: Optional[Callable[[str], None]] = None) -> TopoDS_Shape:
    """Read a STEP file and return its root shape.

    For large assemblies this transfers all roots into a single compound.
    """
    if not os.path.isfile(path):
        raise StepLoadError(f"File not found: {path}")

    if progress:
        progress("Reading STEP header...")

    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise StepLoadError(f"OpenCascade failed to read: {path} (status={status})")

    if progress:
        n_roots = reader.NbRootsForTransfer()
        progress(f"Transferring {n_roots} root(s) into geometry...")

    reader.TransferRoots()

    shape = reader.OneShape()
    if shape.IsNull():
        raise StepLoadError("STEP file produced an empty shape")

    return shape


def shape_diagonal(shape: TopoDS_Shape) -> float:
    """Bounding-box diagonal length, used to derive a sensible mesh deflection."""
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5
