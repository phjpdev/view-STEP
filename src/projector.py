"""3D shape -> 2D polylines using OpenCascade's polygonal HLR.

The polygonal algorithm (HLRBRep_PolyAlgo) requires a triangulated shape but is
orders of magnitude faster than the exact algorithm — essential for STEP files
above ~50 MB. The trade-off is silhouettes built from triangle edges instead of
analytic curves; for visualization this is invisible at typical screen scales.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Optional, Callable

from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCC.Core.HLRAlgo import HLRAlgo_Projector
from OCC.Core.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Compound

Polyline = List[Tuple[float, float]]

# Each view = (eye direction (Z of Ax2), in-plane X reference).
# The HLR projector looks along the Z axis of the gp_Ax2; the resulting 2D
# drawing's X axis is the gp_Ax2 X axis.
VIEWS: Dict[str, Tuple[gp_Dir, gp_Dir]] = {
    "front":  (gp_Dir(0, -1, 0), gp_Dir(1, 0, 0)),
    "back":   (gp_Dir(0, 1, 0),  gp_Dir(-1, 0, 0)),
    "top":    (gp_Dir(0, 0, -1), gp_Dir(1, 0, 0)),
    "bottom": (gp_Dir(0, 0, 1),  gp_Dir(1, 0, 0)),
    "left":   (gp_Dir(1, 0, 0),  gp_Dir(0, 1, 0)),
    "right":  (gp_Dir(-1, 0, 0), gp_Dir(0, -1, 0)),
    "iso":    (gp_Dir(-1, -1, 1), gp_Dir(1, -1, 0)),
}


def _make_projector(view: str) -> HLRAlgo_Projector:
    if view not in VIEWS:
        raise ValueError(f"Unknown view '{view}'. Available: {list(VIEWS)}")
    eye, x_ref = VIEWS[view]
    ax = gp_Ax2(gp_Pnt(0, 0, 0), eye, x_ref)
    return HLRAlgo_Projector(ax)


def mesh_shape(shape: TopoDS_Shape, deflection: float) -> None:
    """Triangulate the shape in-place. Required before polygonal HLR."""
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)


def project(
    shape: TopoDS_Shape,
    view: str,
    deflection: float,
    progress: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[TopoDS_Compound], Optional[TopoDS_Compound]]:
    """Run polygonal HLR; return (visible_edges, hidden_edges) as compounds."""
    if progress:
        progress(f"Meshing (deflection={deflection:.4g})...")
    mesh_shape(shape, deflection)

    if progress:
        progress(f"Computing {view} projection (HLR)...")

    hlr = HLRBRep_PolyAlgo()
    hlr.Load(shape)
    hlr.Projector(_make_projector(view))
    hlr.Update()

    extractor = HLRBRep_PolyHLRToShape()
    extractor.Update(hlr)

    visible = _safe_compound(extractor.VCompound)
    outline = _safe_compound(extractor.OutLineVCompound)
    hidden = _safe_compound(extractor.HCompound)

    visible = _merge(visible, outline)

    return visible, hidden


def _safe_compound(getter) -> Optional[TopoDS_Compound]:
    try:
        comp = getter()
    except Exception:
        return None
    if comp is None or comp.IsNull():
        return None
    return comp


def _merge(a: Optional[TopoDS_Compound], b: Optional[TopoDS_Compound]) -> Optional[TopoDS_Compound]:
    if a is None:
        return b
    if b is None:
        return a
    from OCC.Core.TopoDS import TopoDS_Compound
    from OCC.Core.BRep import BRep_Builder
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    builder.Add(comp, a)
    builder.Add(comp, b)
    return comp


def edges_to_polylines(
    compound: Optional[TopoDS_Compound],
    deflection: float,
) -> List[Polyline]:
    """Discretize every edge of the compound into a 2D polyline (drops Z)."""
    polylines: List[Polyline] = []
    if compound is None:
        return polylines

    explorer = TopExp_Explorer(compound, TopAbs_EDGE)
    while explorer.More():
        edge = explorer.Current()
        try:
            adaptor = BRepAdaptor_Curve(edge)
            disc = GCPnts_QuasiUniformDeflection(adaptor, deflection)
            if disc.IsDone() and disc.NbPoints() >= 2:
                pts = [
                    (disc.Value(i).X(), disc.Value(i).Y())
                    for i in range(1, disc.NbPoints() + 1)
                ]
                polylines.append(pts)
        except Exception:
            pass
        explorer.Next()

    return polylines
