# TopoScope

Visual topology mapper for Linux hardware — think `lspci`/`lshw` meets a diagramming tool.

### What it does (v0)
- **Scan**: Collects a coarse hardware graph on Linux from `lspci`, `lsusb`, and `lscpu` when available.
- **Normalize**: Produces a simple JSON graph with `nodes` and `edges`.
- **View**: Serves an interactive web viewer (Cytoscape) to explore the topology.

### Quick start
1) Install (editable dev mode):

```bash
pip install -e .
```

2) Generate a demo graph (works anywhere):

```bash
toposcope scan --demo --out graph.json
```

3) Open the viewer locally:

```bash
toposcope serve --graph graph.json --port 8080
```

Then visit `http://127.0.0.1:8080/index.html`.

### Linux scanning
On a Linux host, run:

```bash
toposcope scan --out graph.json
```

TopoScope will attempt to use:
- `lspci -mm`
- `lsusb`
- `lscpu -J`

These commands are optional; missing tools are skipped gracefully.

### Roadmap (next)
- Enrich PCI/USB hierarchy (controller → hub → device) and nesting from `-t`/`-v` data
- Add NVMe/SATA, network, GPU-specific attributes
- Export/Import graph, file association and offline viewing
- Robust parsers and test data corpus
- Nice print/PDF export and layout presets

### License
Proprietary — All Rights Reserved.

Copyright (c) 2025 Victor Gregorio. 

This software is provided for evaluation and internal use only. No reproduction, redistribution, public hosting, modification, or commercial use is permitted without prior written permission from the copyright holder.
