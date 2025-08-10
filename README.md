# TopoScope

Visual topology mapper for Linux hardware.

### Installation
Python 3.9+ and pip are required. Use a virtual environment (PEP 668 safe). Then install the recommended system tools for richer scans, and install TopoScope.

#### Install Python and recommended system tools
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3 python3-pip python3-venv pciutils usbutils util-linux

# CentOS / RHEL / Fedora
sudo dnf install -y python3 python3-pip pciutils usbutils util-linux

# openSUSE / SLE
sudo zypper refresh && sudo zypper install -y python3 python3-pip pciutils usbutils util-linux
```

#### Create and activate a virtual env
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

#### Install TopoScope (editable)
```bash
pip install -e .
```

#### Upgrade TopoScope
```bash
git pull --ff-only      # fast forward merge
pip install -e .        # optional; needed if deps/metadata changed
python -c "import toposcope; print(toposcope.__version__)"  # verify version
```

### Usage

#### Scan on Linux:
```bash
toposcope scan --out graph.json
```

#### Serve the viewer locally:
```bash
toposcope serve --graph graph.json --port 8080
# open: http://127.0.0.1:8080/index.html
```


#### Headless (remote server):
```bash
# on your client (e.g., laptop)
ssh -L 8080:127.0.0.1:8080 user@your-server

# on your server
toposcope serve --graph graph.json --port 8080 --no-open-browser
# open locally: http://127.0.0.1:8080/index.html
```

#### Demo mode with dummy data (works anywhere):
```bash
toposcope scan --demo --out graph.json
```

### License
Proprietary — All Rights Reserved.

This software is provided for evaluation and internal use only. No reproduction, redistribution, public hosting, modification, or commercial use is permitted without prior written permission from the copyright holder.
