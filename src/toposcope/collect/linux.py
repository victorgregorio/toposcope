from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from typing import List, Tuple, Optional, Dict

from ..model import Edge, Graph, Node


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _run(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        return out
    except Exception:
        return ""


def _parse_lspci_mm(output: str) -> List[Node]:
    nodes: List[Node] = []
    # lspci -mm format: fields quoted, e.g.
    # 00:00.0 "Host bridge" "Intel Corporation" "440FX - 82441FX PMC [Natoma]" -r 02
    line_re = re.compile(r"^(\S+)\s+\"([^\"]*)\"\s+\"([^\"]*)\"\s+\"([^\"]*)\"(.*)$")
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        m = line_re.match(line)
        if not m:
            continue
        address, cls, vendor, device, rest = m.groups()
        node: Node = {
            "id": f"pci:{address}",
            "kind": "pci-device",
            "label": f"{vendor} {device}",
            "properties": {
                "class": cls,
                "address": address,
            },
        }
        nodes.append(node)
    return nodes


def _parse_lspci_vmm(output: str) -> List[Dict[str, str]]:
    """Parse `lspci -Dvmmnnk` into a list of device dicts.

    The vmm format is machine-friendly: stanza blocks separated by blank lines,
    with key: value entries. Numeric IDs often appear in brackets at the end of
    names, so we best-effort extract them.
    """
    if not output:
        return []
    devices: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            if current.get("Slot"):
                devices.append(current)
            current = {}
            continue
        m = re.match(r"^([^:]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.groups()
        current[key.strip()] = val.strip()
    if current.get("Slot"):
        devices.append(current)

    # post-process vendor/device IDs
    for dev in devices:
        vend = dev.get("Vendor", "")
        devi = dev.get("Device", "")
        m_v = re.search(r"\[([0-9a-fA-F]{4})\]", vend)
        m_d = re.search(r"\[([0-9a-fA-F]{4})\]", devi)
        if m_v and not dev.get("VendorId"):
            dev["VendorId"] = m_v.group(1).lower()
        if m_d and not dev.get("DeviceId"):
            dev["DeviceId"] = m_d.group(1).lower()
    return devices


def _parse_lspci_vv_links(output: str) -> Dict[str, Dict[str, str]]:
    """Parse `lspci -vv` to extract PCIe link width/speed by slot.

    Returns mapping: { slot: { 'pcie_speed': '8GT/s', 'pcie_width': 'x16' } }
    """
    links: Dict[str, Dict[str, str]] = {}
    if not output:
        return links
    slot_re = re.compile(r"^([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]) ")
    cur: Optional[str] = None
    for raw in output.splitlines():
        line = raw.rstrip()
        m = slot_re.match(line)
        if m:
            cur = m.group(1)
            continue
        if cur is None:
            continue
        if "LnkSta:" in line:
            # Example: LnkSta: Speed 8GT/s (ok), Width x16 (ok)
            sp = re.search(r"Speed\s+([0-9.]+)GT/s", line)
            wd = re.search(r"Width\s+(x\d+)", line)
            if cur not in links:
                links[cur] = {}
            if sp:
                links[cur]["pcie_speed"] = f"{sp.group(1)}GT/s"
            if wd:
                links[cur]["pcie_width"] = wd.group(1)
    return links


def _parse_nvme_list_json(output: str) -> List[Node]:
    """Parse `nvme list -o json` into NVMe controller/device nodes.

    We keep this tolerant to schema differences across nvme-cli versions.
    """
    nodes: List[Node] = []
    if not output:
        return nodes
    try:
        data = json.loads(output)
    except Exception:
        return nodes

    devices = []
    if isinstance(data, dict) and "Devices" in data and isinstance(data["Devices"], list):
        devices = data["Devices"]
    elif isinstance(data, list):
        devices = data

    for dev in devices:
        # Try multiple common keys; fall back gently
        name = dev.get("Name") or dev.get("DevicePath") or dev.get("Device") or dev.get("SubsystemNQN") or dev.get("Address")
        model = dev.get("ModelNumber") or dev.get("Model") or dev.get("Model Number") or "NVMe"
        serial = dev.get("SerialNumber") or dev.get("Serial") or ""
        firmware = dev.get("Firmware") or dev.get("FirmwareRevision") or ""
        # Size can be reported under various keys (bytes)
        size_bytes = None
        for k in ("PhysicalSize", "Size", "TotalSize", "Capacity"):
            v = dev.get(k)
            if isinstance(v, (int, float)) and v > 0:
                size_bytes = float(v)
                break
        size_gb = None
        if size_bytes:
            try:
                size_gb = round(size_bytes / (1024.0 ** 3), 1)
            except Exception:
                size_gb = None

        node: Node = {
            "id": f"nvme:{name}" if name else f"nvme:{len(nodes)}",
            "kind": "nvme-device",
            "label": model if model else (name or "NVMe"),
            "properties": {
                "model": model,
                "serial": serial,
                "firmware": firmware,
                **({"size_gb": f"{size_gb:.1f}"} if size_gb is not None else {}),
            },
        }
        nodes.append(node)

    return nodes


def _parse_lsblk_json(output: str) -> List[Node]:
    """Parse `lsblk -J -o NAME,TYPE,SIZE,ROTA,TRAN,MODEL,SERIAL` into disk nodes.

    We only emit top-level disks (TYPE == 'disk'); NVMe disks are often also present
    here but we let NVMe-specific parsing above handle richer details with a
    different kind to avoid id collisions.
    """
    nodes: List[Node] = []
    if not output:
        return nodes
    try:
        data = json.loads(output)
    except Exception:
        return nodes
    devs = (data or {}).get("blockdevices") or []
    for d in devs:
        if (d.get("type") or d.get("type")) != "disk":
            continue
        name = d.get("name") or "disk"
        # Skip NVMe disks (handled by nvme list)
        if name.startswith("nvme"):
            continue
        model = d.get("model") or "Disk"
        size = d.get("size") or ""
        serial = d.get("serial") or ""
        tran = d.get("tran") or ""
        rota = d.get("rota")
        # rota may be int/bool; translate to media type when possible
        media = None
        try:
            if rota is not None:
                media = "hdd" if int(rota) == 1 else "ssd"
        except Exception:
            media = None

        props: Dict[str, str] = {"model": model}
        if size:
            props["size"] = size
        if serial:
            props["serial"] = serial
        if tran:
            props["tran"] = tran
        if media:
            props["media"] = media

        node: Node = {
            "id": f"disk:{name}",
            "kind": "disk-device",
            "label": f"{model} ({name})",
            "properties": props,
        }
        nodes.append(node)

    return nodes


def _parse_lsusb(output: str) -> List[Node]:
    nodes: List[Node] = []
    # lsusb lines like: Bus 001 Device 002: ID 8087:0024 Intel Corp. Integrated Rate Matching Hub
    line_re = re.compile(
        r"^Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s+(.*)$"
    )
    for line in output.splitlines():
        m = line_re.match(line.strip())
        if not m:
            continue
        bus, dev, vid, pid, rest = m.groups()
        node: Node = {
            "id": f"usb:{bus}-{dev}",
            "kind": "usb-device",
            "label": rest.strip(),
            "properties": {
                "bus": bus,
                "device": dev,
                "vendor_id": vid.lower(),
                "product_id": pid.lower(),
            },
        }
        nodes.append(node)
    return nodes


def _parse_lscpu_json(output: str) -> List[Node]:
    nodes: List[Node] = []
    if not output:
        return nodes
    try:
        data = json.loads(output)
        if isinstance(data, dict) and "lscpu" in data:
            entries = {item.get("field", "").strip(': '): item.get("data") for item in data["lscpu"]}
            model_name = entries.get("Model name", "CPU")
            sockets = entries.get("Socket(s)", "1")
            cores_per_socket = entries.get("Core(s) per socket", "?")
            threads_per_core = entries.get("Thread(s) per core", "?")
            node: Node = {
                "id": "cpu:0",
                "kind": "cpu",
                "label": model_name,
                "properties": {
                    "sockets": str(sockets),
                    "cores_per_socket": str(cores_per_socket),
                    "threads_per_core": str(threads_per_core),
                },
            }
            nodes.append(node)
    except Exception:
        pass
    return nodes


def _parse_meminfo_total_kb(output: str) -> Optional[int]:
    if not output:
        return None
    for line in output.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                try:
                    return int(parts[1])
                except Exception:
                    return None
    return None


def _parse_dmidecode_memory(output: str) -> List[Node]:
    """Parse output from 'dmidecode -t memory' to DIMM nodes.

    Note: This may require root; if output is empty, caller should ignore.
    """
    if not output:
        return []
    devices: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            # on blank boundary push if it's a device
            if current.get("__kind__") == "Memory Device":
                devices.append(current)
            current = {}
            continue
        if line.strip() in ("Memory Device", "Physical Memory Array"):
            # starting a new block
            if current.get("__kind__") == "Memory Device":
                devices.append(current)
            current = {"__kind__": line.strip()}
            continue
        # key: value pairs have some indentation
        m = re.match(r"^\s*([^:]+):\s*(.*)$", line)
        if m and current is not None:
            key, val = m.groups()
            current[key.strip()] = val.strip()

    if current.get("__kind__") == "Memory Device":
        devices.append(current)

    nodes: List[Node] = []
    idx = 0
    for dev in devices:
        size = dev.get("Size", "")
        if not size or size.lower().startswith("no module"):
            continue
        locator = dev.get("Locator") or dev.get("Bank Locator") or f"DIMM-{idx}"
        dtype = dev.get("Type", "?")
        speed = dev.get("Speed", "")
        # normalize size to GB string
        size_gb: Optional[float] = None
        m_mb = re.search(r"(\d+)\s*MB", size, re.IGNORECASE)
        m_gb = re.search(r"(\d+)\s*GB", size, re.IGNORECASE)
        if m_gb:
            try:
                size_gb = float(m_gb.group(1))
            except Exception:
                size_gb = None
        elif m_mb:
            try:
                size_gb = float(m_mb.group(1)) / 1024.0
            except Exception:
                size_gb = None

        node: Node = {
            "id": f"dimm:{locator}",
            "kind": "dimm",
            "label": f"DIMM {locator}",
            "properties": {
                "slot": locator,
                "size_gb": (f"{size_gb:.1f}" if size_gb is not None else size),
                "type": dtype,
                "speed": speed,
                "manufacturer": dev.get("Manufacturer", ""),
                "part_number": dev.get("Part Number", ""),
                "serial": dev.get("Serial Number", ""),
            },
        }
        nodes.append(node)
        idx += 1
    return nodes


def collect_linux_hardware_graph() -> Graph:
    nodes: List[Node] = []
    edges: List[Edge] = []

    # Root
    root: Node = {
        "id": "root",
        "kind": "system",
        "label": os.uname().nodename if hasattr(os, "uname") else "Linux",
        "properties": {"os": platform.platform()},
    }
    nodes.append(root)

    # CPU
    if _which("lscpu"):
        cpu_nodes = _parse_lscpu_json(_run(["lscpu", "-J"]))
        for n in cpu_nodes:
            nodes.append(n)
            edges.append({"id": f"e:{root['id']}->{n['id']}", "source": root["id"], "target": n["id"], "kind": "contains", "label": "contains"})

    # Memory summary and DIMMs
    mem_root: Optional[Node] = None
    dimm_nodes: List[Node] = []
    # Try dmidecode for per-DIMM info (may require root)
    if _which("dmidecode"):
        dimm_nodes = _parse_dmidecode_memory(_run(["dmidecode", "-t", "memory"]))
    total_gb: Optional[float] = None
    if dimm_nodes:
        try:
            total_gb = sum(float(n["properties"].get("size_gb", 0)) for n in dimm_nodes if n["properties"].get("size_gb"))
        except Exception:
            total_gb = None
    if total_gb is None:
        # Fallback to /proc/meminfo
        total_kb = _parse_meminfo_total_kb(_run(["cat", "/proc/meminfo"]))
        if total_kb:
            total_gb = round(total_kb / 1024.0 / 1024.0, 1)

    mem_root = {
        "id": "bus:memory",
        "kind": "memory",
        "label": "Memory",
        "properties": {"total_gb": f"{total_gb:.1f}" if total_gb is not None else ""},
    }
    nodes.append(mem_root)
    edges.append({"id": f"e:{root['id']}->bus:memory", "source": root["id"], "target": "bus:memory", "kind": "contains", "label": "contains"})
    for n in dimm_nodes:
        nodes.append(n)
        edges.append({"id": f"e:bus:memory->{n['id']}", "source": "bus:memory", "target": n["id"], "kind": "contains", "label": "dimm"})

    # PCI devices (enriched)
    if _which("lspci"):
        vmm = _parse_lspci_vmm(_run(["lspci", "-Dvmmnnk"]))
        vv_links = _parse_lspci_vv_links(_run(["lspci", "-vv"]))
        pci_root: Node = {
            "id": "bus:pci",
            "kind": "bus",
            "label": "PCI Bus",
            "properties": {},
        }
        nodes.append(pci_root)
        edges.append({"id": f"e:{root['id']}->bus:pci", "source": root["id"], "target": "bus:pci", "kind": "contains", "label": "contains"})
        for dev in vmm:
            slot = dev.get("Slot") or "?"
            cls = dev.get("Class") or dev.get("ClassName") or ""
            vendor = dev.get("Vendor", "").split(" [")[0]
            device = dev.get("Device", "").split(" [")[0]
            vid = dev.get("VendorId", "")
            did = dev.get("DeviceId", "")
            props: Dict[str, str] = {
                "class": cls,
                "address": slot,
            }
            if vid:
                props["vendor_id"] = vid
            if did:
                props["device_id"] = did
            link = vv_links.get(slot)
            if link:
                props.update(link)
            node: Node = {
                "id": f"pci:{slot}",
                "kind": "pci-device",
                "label": f"{vendor} {device}".strip() or slot,
                "properties": props,
            }
            nodes.append(node)
            edges.append({"id": f"e:bus:pci->pci:{slot}", "source": "bus:pci", "target": f"pci:{slot}", "kind": "contains", "label": "device"})

    # USB devices
    if _which("lsusb"):
        usb_nodes = _parse_lsusb(_run(["lsusb"]))
        usb_root: Node = {
            "id": "bus:usb",
            "kind": "bus",
            "label": "USB Bus",
            "properties": {},
        }
        nodes.append(usb_root)
        edges.append({"id": f"e:{root['id']}->bus:usb", "source": root["id"], "target": "bus:usb", "kind": "contains", "label": "contains"})
        for n in usb_nodes:
            nodes.append(n)
            edges.append({"id": f"e:bus:usb->{n['id']}", "source": "bus:usb", "target": n["id"], "kind": "contains", "label": "device"})

    # Storage: NVMe and generic disks
    storage_root: Node = {
        "id": "bus:storage",
        "kind": "bus",
        "label": "Storage",
        "properties": {},
    }
    nodes.append(storage_root)
    edges.append({"id": f"e:{root['id']}->bus:storage", "source": root["id"], "target": "bus:storage", "kind": "contains", "label": "contains"})

    # NVMe via nvme-cli
    if _which("nvme"):
        nvme_nodes = _parse_nvme_list_json(_run(["nvme", "list", "-o", "json"]))
        for n in nvme_nodes:
            nodes.append(n)
            edges.append({"id": f"e:bus:storage->{n['id']}", "source": "bus:storage", "target": n["id"], "kind": "contains", "label": "nvme"})

    # Generic disks via lsblk
    if _which("lsblk"):
        lsblk_nodes = _parse_lsblk_json(_run(["lsblk", "-J", "-o", "NAME,TYPE,SIZE,ROTA,TRAN,MODEL,SERIAL"]))
        for n in lsblk_nodes:
            nodes.append(n)
            edges.append({"id": f"e:bus:storage->{n['id']}", "source": "bus:storage", "target": n["id"], "kind": "contains", "label": "disk"})

    return {"nodes": nodes, "edges": edges}


def generate_demo_graph() -> Graph:
    nodes: List[Node] = [
        {"id": "root", "kind": "system", "label": "Demo System", "properties": {"os": "Linux"}},
        {"id": "cpu:0", "kind": "cpu", "label": "Intel(R) Xeon(R) CPU", "properties": {"sockets": "1", "cores_per_socket": "8", "threads_per_core": "2"}},
        {"id": "bus:memory", "kind": "memory", "label": "Memory", "properties": {"total_gb": "32.0"}},
        {"id": "dimm:A1", "kind": "dimm", "label": "DIMM A1", "properties": {"slot": "A1", "size_gb": "16.0", "type": "DDR4", "speed": "2666 MT/s"}},
        {"id": "dimm:B1", "kind": "dimm", "label": "DIMM B1", "properties": {"slot": "B1", "size_gb": "16.0", "type": "DDR4", "speed": "2666 MT/s"}},
        {"id": "bus:pci", "kind": "bus", "label": "PCI Bus", "properties": {}},
        {"id": "pci:00:00.0", "kind": "pci-device", "label": "Intel Corporation 440FX - 82441FX PMC [Natoma]", "properties": {"class": "Host bridge", "address": "00:00.0"}},
        {"id": "pci:00:01.0", "kind": "pci-device", "label": "Intel Corporation 82371SB PIIX3 ISA [Natoma/Triton II]", "properties": {"class": "ISA bridge", "address": "00:01.0"}},
        {"id": "bus:usb", "kind": "bus", "label": "USB Bus", "properties": {}},
        {"id": "usb:001-002", "kind": "usb-device", "label": "Intel Corp. Integrated Hub", "properties": {"bus": "001", "device": "002", "vendor_id": "8087", "product_id": "0024"}},
    ]
    edges: List[Edge] = [
        {"id": "e:root->cpu:0", "source": "root", "target": "cpu:0", "kind": "contains", "label": "contains"},
        {"id": "e:root->bus:memory", "source": "root", "target": "bus:memory", "kind": "contains", "label": "contains"},
        {"id": "e:bus:memory->dimm:A1", "source": "bus:memory", "target": "dimm:A1", "kind": "contains", "label": "dimm"},
        {"id": "e:bus:memory->dimm:B1", "source": "bus:memory", "target": "dimm:B1", "kind": "contains", "label": "dimm"},
        {"id": "e:root->bus:pci", "source": "root", "target": "bus:pci", "kind": "contains", "label": "contains"},
        {"id": "e:bus:pci->pci:00:00.0", "source": "bus:pci", "target": "pci:00:00.0", "kind": "contains", "label": "device"},
        {"id": "e:bus:pci->pci:00:01.0", "source": "bus:pci", "target": "pci:00:01.0", "kind": "contains", "label": "device"},
        {"id": "e:root->bus:usb", "source": "root", "target": "bus:usb", "kind": "contains", "label": "contains"},
        {"id": "e:bus:usb->usb:001-002", "source": "bus:usb", "target": "usb:001-002", "kind": "contains", "label": "device"},
    ]
    return {"nodes": nodes, "edges": edges}
