from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from typing import List

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
        {"id": "bus:pci", "kind": "bus", "label": "PCI Bus", "properties": {}},
        {"id": "pci:00:00.0", "kind": "pci-device", "label": "Intel Corporation 440FX - 82441FX PMC [Natoma]", "properties": {"class": "Host bridge", "address": "00:00.0"}},
        {"id": "pci:00:01.0", "kind": "pci-device", "label": "Intel Corporation 82371SB PIIX3 ISA [Natoma/Triton II]", "properties": {"class": "ISA bridge", "address": "00:01.0"}},
        {"id": "bus:usb", "kind": "bus", "label": "USB Bus", "properties": {}},
        {"id": "usb:001-002", "kind": "usb-device", "label": "Intel Corp. Integrated Hub", "properties": {"bus": "001", "device": "002", "vendor_id": "8087", "product_id": "0024"}},
    ]
    edges: List[Edge] = [
        {"id": "e:root->cpu:0", "source": "root", "target": "cpu:0", "kind": "contains", "label": "contains"},
        {"id": "e:root->bus:pci", "source": "root", "target": "bus:pci", "kind": "contains", "label": "contains"},
        {"id": "e:bus:pci->pci:00:00.0", "source": "bus:pci", "target": "pci:00:00.0", "kind": "contains", "label": "device"},
        {"id": "e:bus:pci->pci:00:01.0", "source": "bus:pci", "target": "pci:00:01.0", "kind": "contains", "label": "device"},
        {"id": "e:root->bus:usb", "source": "root", "target": "bus:usb", "kind": "contains", "label": "contains"},
        {"id": "e:bus:usb->usb:001-002", "source": "bus:usb", "target": "usb:001-002", "kind": "contains", "label": "device"},
    ]
    return {"nodes": nodes, "edges": edges}
