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


def load_step_labels(path: str) -> list:
    """Read component names and their 3D bounding-box centres via XDE.

    Returns a list of (name, (cx, cy, cz)).  Falls back to [] if XDE is
    unavailable or the STEP file has no named components.
    """
    try:
        from OCC.Core.STEPCAFControl import STEPCAFControl_Reader as _CAFReader
        from OCC.Core.XCAFApp import XCAFApp_Application
        from OCC.Core.TDocStd import TDocStd_Document
        from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
        from OCC.Core.TDF import TDF_LabelSequence
        from OCC.Core.TDataStd import TDataStd_Name

        app = XCAFApp_Application.GetApplication()
        doc = TDocStd_Document("BinXCAF")
        app.NewDocument("BinXCAF", doc)

        reader = _CAFReader()
        reader.ReadFile(path)
        reader.Transfer(doc)

        shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
        roots = TDF_LabelSequence()
        shape_tool.GetFreeShapes(roots)

        results: list = []
        for i in range(1, roots.Length() + 1):
            _walk_label(roots.Value(i), shape_tool, TDataStd_Name, results, depth=0)
        return results
    except Exception:
        return []


def _walk_label(label, shape_tool, TDataStd_Name, results: list, depth: int) -> None:
    if depth > 5:
        return
    try:
        attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID(), attr):
            name = attr.Get().ToCString().strip()
            if name:
                shape = shape_tool.GetShape(label)
                if not shape.IsNull():
                    box = Bnd_Box()
                    brepbndlib.Add(shape, box)
                    if not box.IsVoid():
                        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
                        results.append((
                            name,
                            ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2),
                        ))
    except Exception:
        pass
    try:
        from OCC.Core.TDF import TDF_LabelSequence
        comps = TDF_LabelSequence()
        shape_tool.GetComponents(label, comps)
        for i in range(1, comps.Length() + 1):
            _walk_label(comps.Value(i), shape_tool, TDataStd_Name, results, depth + 1)
    except Exception:
        pass


def shape_diagonal(shape: TopoDS_Shape) -> float:
    """Bounding-box diagonal length, used to derive a sensible mesh deflection."""
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5
