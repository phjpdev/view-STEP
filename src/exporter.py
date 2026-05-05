"""Export 2D polylines to SVG / DXF."""

from __future__ import annotations

from typing import Iterable, List, Tuple, Optional

Polyline = List[Tuple[float, float]]


def _bounds(polylines: Iterable[Polyline]):
    xs, ys = [], []
    for pl in polylines:
        for x, y in pl:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def export_svg(
    path: str,
    visible: List[Polyline],
    hidden: Optional[List[Polyline]] = None,
) -> None:
    hidden = hidden or []
    bounds = _bounds(list(visible) + list(hidden))
    if bounds is None:
        raise ValueError("Nothing to export.")
    minx, miny, maxx, maxy = bounds
    margin = max(maxx - minx, maxy - miny) * 0.05 or 1.0
    minx -= margin; miny -= margin
    maxx += margin; maxy += margin
    w, h = maxx - minx, maxy - miny

    # SVG Y axis points down; flip Y so the drawing is right-side up.
    def path_d(pl: Polyline) -> str:
        x0, y0 = pl[0]
        parts = [f"M {x0:.4f} {-y0:.4f}"]
        for x, y in pl[1:]:
            parts.append(f"L {x:.4f} {-y:.4f}")
        return " ".join(parts)

    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{minx:.4f} {-maxy:.4f} {w:.4f} {h:.4f}" '
            f'width="{w:.2f}mm" height="{h:.2f}mm">\n'
        )
        f.write('<g fill="none" stroke="black" stroke-width="0.3" '
                'stroke-linecap="round" stroke-linejoin="round">\n')
        for pl in visible:
            f.write(f'  <path d="{path_d(pl)}"/>\n')
        f.write('</g>\n')
        if hidden:
            f.write('<g fill="none" stroke="#888" stroke-width="0.2" '
                    'stroke-dasharray="2,1" stroke-linecap="round">\n')
            for pl in hidden:
                f.write(f'  <path d="{path_d(pl)}"/>\n')
            f.write('</g>\n')
        f.write('</svg>\n')


def export_dxf(
    path: str,
    visible: List[Polyline],
    hidden: Optional[List[Polyline]] = None,
) -> None:
    try:
        import ezdxf
    except ImportError as e:
        raise RuntimeError("ezdxf is required for DXF export. pip install ezdxf") from e

    hidden = hidden or []
    doc = ezdxf.new("R2010", setup=True)  # setup=True loads standard linetypes (HIDDEN, etc.)
    msp = doc.modelspace()

    if "VISIBLE" not in doc.layers:
        doc.layers.add("VISIBLE", color=7)
    if "HIDDEN" not in doc.layers:
        doc.layers.add("HIDDEN", color=8, linetype="HIDDEN")

    for pl in visible:
        if len(pl) >= 2:
            msp.add_lwpolyline(pl, dxfattribs={"layer": "VISIBLE"})
    for pl in hidden:
        if len(pl) >= 2:
            msp.add_lwpolyline(pl, dxfattribs={"layer": "HIDDEN", "linetype": "HIDDEN"})

    doc.saveas(path)
