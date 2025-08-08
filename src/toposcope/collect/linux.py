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

    # PCI devices
    if _which("lspci"):
        pci_nodes = _parse_lspci_mm(_run(["lspci", "-mm"]))
        pci_root: Node = {
            "id": "bus:pci",
            "kind": "bus",
            "label": "PCI Bus",
            "properties": {},
        }
        nodes.append(pci_root)
        edges.append({"id": f"e:{root['id']}->bus:pci", "source": root["id"], "target": "bus:pci", "kind": "contains", "label": "contains"})
        for n in pci_nodes:
            nodes.append(n)
            edges.append({"id": f"e:bus:pci->{n['id']}", "source": "bus:pci", "target": n["id"], "kind": "contains", "label": "device"})

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
