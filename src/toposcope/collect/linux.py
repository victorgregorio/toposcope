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


def _parse_ip_link_brief(output: str) -> List[Dict[str, str]]:
    """Parse `ip -br link` output into a list of interfaces.

    Returns list of { ifname, state, mac }
    """
    ifaces: List[Dict[str, str]] = []
    if not output:
        return ifaces
    # Lines like: "eth0             UP             52:54:00:12:34:56 ..."
    line_re = re.compile(r"^(\S+)\s+(\S+)\s+([0-9a-fA-F:]{17})")
    for raw in output.splitlines():
        line = raw.strip()
        m = line_re.match(line)
        if not m:
            continue
        ifname_raw, state, mac = m.groups()
        ifname = ifname_raw.split('@', 1)[0]
        ifaces.append({"ifname": ifname, "state": state, "mac": mac.lower()})
    return ifaces


def _parse_ethtool_i(output: str) -> Dict[str, str]:
    """Parse `ethtool -i <ifname>` for driver and bus-info (PCI address)."""
    info: Dict[str, str] = {}
    if not output:
        return info
    for line in output.splitlines():
        if line.lower().startswith("driver:"):
            info["driver"] = line.split(':', 1)[1].strip()
        elif line.lower().startswith("bus-info:"):
            info["bus_info"] = line.split(':', 1)[1].strip()
    return info


def _parse_ethtool_speed(output: str) -> Optional[int]:
    """Parse `ethtool <ifname>` to extract speed in Mbps, if present."""
    if not output:
        return None
    m = re.search(r"Speed:\s*([0-9]+)\s*Mb/s", output)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m = re.search(r"Speed:\s*([0-9]+)\s*Gb/s", output)
    if m:
        try:
            return int(m.group(1)) * 1000
        except Exception:
            return None
    return None


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
            # Flatten lscpu JSON, including nested children
            def walk(items, acc: Dict[str, str]):
                for item in items or []:
                    field = str(item.get("field", "")).strip(': ')
                    value = item.get("data")
                    if field:
                        acc[field] = value
                    children = item.get("children")
                    if children:
                        walk(children, acc)

            entries: Dict[str, str] = {}
            walk(data["lscpu"], entries)
            model_name = entries.get("Model name", "CPU")
            sockets = entries.get("Socket(s)", "1")
            cores_per_socket = entries.get("Core(s) per socket", "?")
            threads_per_core = entries.get("Thread(s) per core", "?")
            vendor_id = entries.get("Vendor ID") or entries.get("Vendor ID:") or entries.get("Vendor")
            cpu_family = entries.get("CPU family")
            model = entries.get("Model")
            stepping = entries.get("Stepping")
            def clean_mhz(v: Optional[str]) -> Optional[str]:
                if not v:
                    return None
                if isinstance(v, str) and v.strip() == "-":
                    return None
                return str(v)

            min_mhz = clean_mhz(entries.get("CPU min MHz") or entries.get("CPU min MHz:"))
            max_mhz = clean_mhz(entries.get("CPU max MHz") or entries.get("CPU max MHz:"))
            # Do not capture point-in-time current frequency
            current_mhz = None
            l1d = entries.get("L1d cache")
            l1i = entries.get("L1i cache")
            l2 = entries.get("L2 cache")
            l3 = entries.get("L3 cache")
            # Totals (sum of all) if available
            l1d_total = entries.get("L1d")
            l1i_total = entries.get("L1i")
            l2_total = entries.get("L2")
            address_sizes = entries.get("Address sizes")
            virtualization = entries.get("Virtualization")
            hypervisor = entries.get("Hypervisor vendor")

            # Derive base_mhz from dmidecode Max/Current (treat as base, not turbo)
            base_mhz = None
            if _which("dmidecode"):
                dmi_cur, dmi_max = _parse_dmidecode_processor(_run(["dmidecode", "-t", "processor"]))
                base_mhz = dmi_max or dmi_cur

            node: Node = {
                "id": "cpu:0",
                "kind": "cpu",
                "label": model_name,
                "properties": {
                    "sockets": str(sockets),
                    "cores_per_socket": str(cores_per_socket),
                    "threads_per_core": str(threads_per_core),
                    **({"vendor_id": str(vendor_id)} if vendor_id else {}),
                    **({"family": str(cpu_family)} if cpu_family else {}),
                    **({"model": str(model)} if model else {}),
                    **({"stepping": str(stepping)} if stepping else {}),
                    **({"min_mhz": str(min_mhz)} if min_mhz else {}),
                    **({"max_mhz": str(max_mhz)} if max_mhz else {}),
                    **({"base_mhz": str(base_mhz)} if base_mhz else {}),
                    **({"l1d_cache": str(l1d)} if l1d else {}),
                    **({"l1i_cache": str(l1i)} if l1i else {}),
                    **({"l2_cache": str(l2)} if l2 else {}),
                    **({"l3_cache": str(l3)} if l3 else {}),
                    **({"l1d_total": str(l1d_total)} if l1d_total else {}),
                    **({"l1i_total": str(l1i_total)} if l1i_total else {}),
                    **({"l2_total": str(l2_total)} if l2_total else {}),
                    **({"address_sizes": str(address_sizes)} if address_sizes else {}),
                    **({"virtualization": str(virtualization)} if virtualization else {}),
                    **({"hypervisor": str(hypervisor)} if hypervisor else {}),
                },
            }
            nodes.append(node)
    except Exception:
        pass
    return nodes


def _parse_cpuinfo_current_mhz(output: str) -> Optional[str]:
    if not output:
        return None
    m = re.search(r"^cpu MHz\s*:\s*([0-9]+(?:\.[0-9]+)?)", output, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def _parse_dmidecode_processor(output: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (current_mhz, max_mhz) strings if present."""
    if not output:
        return (None, None)
    cur = None
    mx = None
    for line in output.splitlines():
        line = line.strip()
        m1 = re.match(r"Current Speed:\s*([0-9]+)\s*MHz", line)
        if m1:
            cur = m1.group(1)
        m2 = re.match(r"Max Speed:\s*([0-9]+)\s*MHz", line)
        if m2:
            mx = m2.group(1)
        if cur and mx:
            break
    return (cur, mx)


def _collect_numa_nodes() -> List[Node]:
    """Collect NUMA nodes from sysfs; fallback to numactl if needed.

    Returns a list of `numa-node` nodes with properties cpus and mem_total_gb.
    """
    nodes: List[Node] = []
    base = "/sys/devices/system/node"
    try:
        import os
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if not name.startswith("node"):
                    continue
                path = os.path.join(base, name)
                try:
                    with open(os.path.join(path, "cpulist"), "r", encoding="utf-8") as f:
                        cpulist = f.read().strip()
                except Exception:
                    cpulist = ""
                mem_total_gb = ""
                try:
                    with open(os.path.join(path, "meminfo"), "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("Node") and "MemTotal" in line:
                                parts = line.split()
                                # ... MemTotal: <value> kB
                                for i, tok in enumerate(parts):
                                    if tok == "MemTotal:" and i + 1 < len(parts):
                                        try:
                                            kb = float(parts[i + 1])
                                            mem_total_gb = f"{kb / 1024.0 / 1024.0:.1f}"
                                        except Exception:
                                            pass
                                break
                except Exception:
                    pass
                node: Node = {
                    "id": f"numa:{name[4:]}",
                    "kind": "numa-node",
                    "label": f"NUMA {name[4:]}",
                    "properties": {"cpus": cpulist, **({"mem_total_gb": mem_total_gb} if mem_total_gb else {})},
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
        # Prefer configured speed; fall back to reported/max
        speed = (
            dev.get("Configured Memory Speed")
            or dev.get("Configured Clock Speed")
            or dev.get("Speed")
            or dev.get("Maximum Speed")
            or ""
        )
        if speed and isinstance(speed, str) and speed.strip().lower() in ("unknown", "unconfigured", "n/a"):
            speed = ""
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
                **({"speed": speed} if speed else {}),
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
        # NUMA nodes linked under CPU if present
        if cpu_nodes:
            numa_nodes = _collect_numa_nodes()
            for nn in numa_nodes:
                nodes.append(nn)
                edges.append({"id": f"e:{cpu_nodes[0]['id']}->{nn['id']}", "source": cpu_nodes[0]["id"], "target": nn["id"], "kind": "contains", "label": "numa"})

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
        created_pci_nodes: Dict[str, Node] = {}
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
            kind = "pci-device"
            cls_l = cls.lower()
            if any(k in cls_l for k in ["vga", "3d controller", "display controller", "processing accelerators", "accelerator", "co-processor"]):
                kind = "gpu-device"
            node: Node = {
                "id": f"pci:{slot}",
                "kind": kind,
                "label": f"{vendor} {device}".strip() or slot,
                "properties": props,
            }
            nodes.append(node)
            edges.append({"id": f"e:bus:pci->pci:{slot}", "source": "bus:pci", "target": f"pci:{slot}", "kind": "contains", "label": "device"})
            created_pci_nodes[slot] = node
            m_tail = re.search(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$", slot)
            if m_tail:
                created_pci_nodes[m_tail.group(1)] = node

        # NVIDIA enrichment via nvidia-smi (maps by PCI bus id)
        if _which("nvidia-smi") and created_pci_nodes:
            out = _run([
                "nvidia-smi",
                "--query-gpu=pci.bus_id,name,driver_version,memory.total,temperature.gpu,power.draw,power.limit,utilization.gpu",
                "--format=csv,noheader,nounits",
            ])
            if out:
                for line in out.splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 2:
                        continue
                    bus_id = parts[0]
                    # Expect forms like 00000000:01:00.0 or 0000:65:00.0
                    m_tail = re.search(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$", bus_id)
                    m_full = re.search(r"([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])", bus_id)
                    node = None
                    if m_tail:
                        node = created_pci_nodes.get(m_tail.group(1))
                    if node is None and m_full:
                        node = created_pci_nodes.get(m_full.group(1))
                    if node is None:
                        continue
                    if not node:
                        continue
                    name = parts[1] if len(parts) > 1 else ""
                    driver = parts[2] if len(parts) > 2 else ""
                    vram_mb = parts[3] if len(parts) > 3 else ""
                    temp_c = parts[4] if len(parts) > 4 else ""
                    power_w = parts[5] if len(parts) > 5 else ""
                    power_cap = parts[6] if len(parts) > 6 else ""
                    util_gpu = parts[7] if len(parts) > 7 else ""
                    node["kind"] = "gpu-device"
                    if name:
                        node["label"] = name
                    p = node.setdefault("properties", {})
                    if driver:
                        p["driver"] = driver
                    if vram_mb:
                        p["vram_mb"] = vram_mb
                    if temp_c:
                        p["temperature_c"] = temp_c
                    if power_w:
                        p["power_w"] = power_w
                    if power_cap:
                        p["power_cap_w"] = power_cap
                    if util_gpu:
                        p["utilization_gpu_pct"] = util_gpu

        # AMD ROCm enrichment via rocm-smi JSON
        def _parse_rocm_smi_json(output: str) -> List[Dict[str, str]]:
            devices: List[Dict[str, str]] = []
            if not output:
                return devices
            try:
                data = json.loads(output)
            except Exception:
                return devices
            # rocm-smi -a --json often returns a dict of cards {"card0": {...}, ...}
            if isinstance(data, dict):
                items = data.items()
            elif isinstance(data, list):
                # some versions might return a list
                items = [(str(i), v) for i, v in enumerate(data)]
            else:
                items = []
            for _, info in items:
                if not isinstance(info, dict):
                    continue
                # flatten nested dicts into a single-level map of key->value strings
                flat: Dict[str, str] = {}
                def walk(prefix: str, obj: Dict[str, object]):
                    for k, v in obj.items():
                        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
                        if isinstance(v, dict):
                            walk(key, v)
                        else:
                            flat[key] = str(v)
                walk("", info)
                # heuristic extraction
                def find_key(substrs: List[str]) -> Optional[str]:
                    # return the first match scanning keys in sorted order for stability
                    for k in sorted(flat.keys()):
                        lk = k.lower()
                        if all(s in lk for s in substrs):
                            return flat[k]
                    return None

                # Identify PCI BDF as precisely as possible
                bdf = (
                    find_key(["pci bus"]) or
                    find_key(["pcie", "bus"]) or
                    find_key(["bdf"]) or
                    find_key(["pci", "bus"]) or
                    ""
                )
                # Prefer product/series/name over hex model code
                name = (
                    find_key(["device name"]) or
                    find_key(["card series"]) or
                    find_key(["product"]) or
                    find_key(["name"]) or
                    find_key(["card model"]) or
                    ""
                )
                driver = find_key(["driver version"]) or ""

                # Prefer hotspot/core temperature then memory
                temp_c = (
                    find_key(["temperature_hotspot", "c"]) or
                    find_key(["temperature (sensor junction)", "c"]) or
                    find_key(["temperature (sensor memory)", "c"]) or
                    find_key(["temperature", "c"]) or
                    ""
                )
                # Prefer current socket power, fallback to generic current power; capture max separately
                power_current = (
                    find_key(["current socket graphics package power", "w"]) or
                    find_key(["current_socket_power", "w"]) or
                    find_key(["socket graphics package power", "w"]) or
                    ""
                )
                power_cap = find_key(["max graphics package power", "w"]) or ""
                util_gpu = find_key(["gpu use", "%"]) or find_key(["average_gfx_activity", "%"]) or ""
                vram_b = find_key(["vram", "total"]) or find_key(["memory", "total"]) or ""
                vram_mb: Optional[str] = None
                if vram_b:
                    try:
                        # value may include units; extract digits
                        num = re.search(r"([0-9]+)", vram_b)
                        if num:
                            val = int(num.group(1))
                            # Heuristic: if very large, assume bytes; else kB
                            if val > 1_000_000_000:
                                vram_mb = str(int(round(val / (1024*1024))))
                            else:
                                vram_mb = str(int(round(val / 1024)))
                    except Exception:
                        pass
                # PCIe link metrics: convert 0.1 GT/s to GT/s
                pcie_width = find_key(["pcie_link_width", "lanes"]) or ""
                pcie_speed_raw = find_key(["pcie_link_speed", "gt/s"]) or ""
                pcie_speed = None
                if pcie_speed_raw:
                    m = re.search(r"([0-9]+)", pcie_speed_raw)
                    if m:
                        try:
                            pcie_speed = f"{int(m.group(1))/10:.1f}GT/s"
                        except Exception:
                            pcie_speed = None
                devices.append({
                    "bdf": bdf or "",
                    "name": name or "",
                    "driver": driver or "",
                    "temperature_c": temp_c or "",
                    "power_w": power_current or "",
                    **({"power_cap_w": power_cap} if power_cap else {}),
                    **({"vram_mb": vram_mb} if vram_mb else {}),
                    **({"pcie_width": f"x{pcie_width}"} if pcie_width and not pcie_width.startswith("x") else ({"pcie_width": pcie_width} if pcie_width else {})),
                    **({"pcie_speed": pcie_speed} if pcie_speed else {}),
                    **({"utilization_gpu_pct": util_gpu} if util_gpu else {}),
                })
            return devices

        if _which("rocm-smi") and created_pci_nodes:
            out = _run(["rocm-smi", "--showall", "--json"]) or _run(["rocm-smi", "-a", "--json"])  # try variants
            for dev in _parse_rocm_smi_json(out):
                bdf = dev.get("bdf", "")
                # normalize to slot form 00:00.0 at end of BDF
                m = re.search(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$", bdf)
                if not m:
                    continue
                slot_key = m.group(1)
                node = created_pci_nodes.get(slot_key) or created_pci_nodes.get(bdf)
                if not node:
                    continue
                node["kind"] = "gpu-device"
                if dev.get("name"):
                    node["label"] = dev["name"]
                p = node.setdefault("properties", {})
                for k in ("driver", "vram_mb", "temperature_c", "power_w"):
                    if dev.get(k):
                        p[k] = dev[k]

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

    # Network interfaces
    if _which("ip"):
        net_root: Node = {"id": "bus:net", "kind": "bus", "label": "Network", "properties": {}}
        nodes.append(net_root)
        edges.append({"id": f"e:{root['id']}->bus:net", "source": root["id"], "target": "bus:net", "kind": "contains", "label": "contains"})
        ifaces = _parse_ip_link_brief(_run(["ip", "-br", "link"]))
        for iface in ifaces:
            ifname = iface.get("ifname")
            props: Dict[str, str] = {k: v for k, v in iface.items() if k != "ifname"}
            # enrich with ethtool
            if _which("ethtool") and ifname:
                info = _parse_ethtool_i(_run(["ethtool", "-i", ifname]))
                speed = _parse_ethtool_speed(_run(["ethtool", ifname]))
                props.update(info)
                if speed:
                    props["speed_mbps"] = str(speed)
            node: Node = {
                "id": f"net:{ifname}",
                "kind": "net-interface",
                "label": ifname,
                "properties": props,
            }
            nodes.append(node)
            edges.append({"id": f"e:bus:net->net:{ifname}", "source": "bus:net", "target": f"net:{ifname}", "kind": "contains", "label": "iface"})

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
