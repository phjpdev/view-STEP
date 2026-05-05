# view-step

Load 3D STEP / STP files and view them as 2D engineering drawings (orthographic
projections with proper hidden-line removal). Built for large files — STEP
assemblies of 100 MB+ are the target use case.

## How it works

1. **Load** the STEP file with OpenCascade (`pythonocc-core`).
2. **Mesh** the shape (`BRepMesh_IncrementalMesh`) at a deflection auto-derived
   from the bounding-box diagonal.
3. **Project** with the polygonal HLR algorithm (`HLRBRep_PolyAlgo`) — much
   faster than the exact algorithm and the only practical choice for big files.
4. **Discretize** the resulting projected edges into 2D polylines.
5. **Render** in a PyQt5 window with a matplotlib canvas (zoom / pan / save).
6. **Export** to SVG, DXF, or PNG.

All I/O and HLR work runs on background `QThread`s, so the UI stays responsive
even while a 100 MB file is being processed.

## Install

### Option A — conda (recommended)

```powershell
conda env create -f environment.yml
conda activate view-step
python main.py
```

### Option B — pip

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

`pythonocc-core` pip wheels exist for common platforms but are less reliable
than the conda-forge build. If `pip install pythonocc-core` fails, use conda.

## Usage

1. **Open STEP...** (Ctrl+O) — pick your `.step` / `.stp` file.
2. The bounding-box diagonal is measured; mesh **Deflection** is set to 0.1% of
   it. For a 100 MB file the first projection may take 30 s – several minutes;
   subsequent view changes are faster (mesh is reused).
3. **View** — switch between front / back / top / bottom / left / right / iso.
4. **Hidden Lines** — toggle dashed hidden edges.
5. **Export SVG / DXF / PNG** — save the current 2D view.

### Tuning for large files

- Increase **Deflection** (e.g. 5× the auto value) to make the first run faster
  at the cost of curved-edge fidelity. Click **Recompute** after changing it.
- The matplotlib toolbar's pan/zoom uses an interactive cache; very dense views
  (millions of edges) may feel sluggish — export to SVG for crisp viewing in a
  browser instead.

## Project layout

```
view-step/
├── main.py              # entry point
├── requirements.txt
├── environment.yml
├── README.md
└── src/
    ├── loader.py        # STEP I/O + bbox helpers
    ├── projector.py     # mesh + polygonal HLR + edge discretization
    ├── exporter.py      # SVG / DXF writers
    └── viewer.py        # PyQt5 UI, threaded workers
```
