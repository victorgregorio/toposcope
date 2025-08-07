# TopoScope

Visual topology mapper for Linux hardware.

### Installation
Python 3.9+ and pip are required.

Optional (for richer scans): `lspci`, `lsusb`, `lscpu`.

Install system tools (optional but recommended):
- Ubuntu/Debian:
  ```bash
  sudo apt update && sudo apt install -y pciutils usbutils util-linux
  ```
- CentOS / RHEL / Fedora:
  ```bash
  sudo dnf install -y pciutils usbutils util-linux
  # or on older systems: sudo yum install -y pciutils usbutils util-linux
  ```
- openSUSE / SLE:
  ```bash
  sudo zypper refresh && sudo zypper install -y pciutils usbutils util-linux
  ```

Install TopoScope (editable dev mode):
```bash
pip install -e .
```

### Usage
Demo (works anywhere):
```bash
toposcope scan --demo --out graph.json
```

Scan on Linux:
```bash
toposcope scan --out graph.json
```

Serve the viewer locally:
```bash
toposcope serve --graph graph.json --port 8080
```
Open: `http://127.0.0.1:8080/index.html`

Headless (remote server):
```bash
# on server
toposcope serve --graph graph.json --port 8080 --no-open-browser
# on your laptop
ssh -L 8080:127.0.0.1:8080 user@your-server
```
Open locally: `http://127.0.0.1:8080/index.html`

### License
Proprietary — All Rights Reserved.

This software is provided for evaluation and internal use only. No reproduction, redistribution, public hosting, modification, or commercial use is permitted without prior written permission from the copyright holder.
