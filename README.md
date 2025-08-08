# TopoScope

Visual topology mapper for Linux hardware.

### Installation
Python 3.9+ and pip are required. Use a virtual environment (PEP 668 safe):

Distro quick setup:
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# CentOS / RHEL / Fedora
sudo dnf install -y python3 python3-pip

# openSUSE / SLE
sudo zypper refresh && sudo zypper install -y python3 python3-pip
```

Create and activate a virtual env:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install TopoScope (editable):
```bash
pip install -e .
```

Optional system tools (better scans):
```bash
# Ubuntu/Debian
sudo apt install -y pciutils usbutils util-linux
# CentOS / RHEL / Fedora
sudo dnf install -y pciutils usbutils util-linux
# openSUSE / SLE
sudo zypper install -y pciutils usbutils util-linux
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
