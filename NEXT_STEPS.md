### TopoScope: Next Steps

Keep KISS. Default scans stay fast and no-root; deeper details are opt-in.

### Implemented (current)
- CPU
  - Source: `lscpu -J`, `dmidecode -t processor`
  - Collected: sockets, cores_per_socket, threads_per_core, vendor/family/model/stepping, min/max MHz (static), base_mhz (dmidecode), cache sizes and totals, address_sizes, virtualization, hypervisor, CPU serial (when exposed)
- Memory
  - Source: `/proc/meminfo`, `dmidecode -t memory`
  - Collected: total_gb, per-DIMM size_gb, type, manufacturer, part_number, serial, speed (configured/reported if available)
- PCI
  - Source: `lspci -Dvmmnnk`, `lspci -vv`
  - Collected: class, address, vendor_id/device_id, PCIe link speed/width
- USB: `lsusb`
- Storage
  - NVMe from `nvme list -o json` (model, serial, firmware, size)
  - Disks from `lsblk -J` (model, size, serial, transport, media)
- Network: `ip -br link`, `ethtool` (driver, speed)
- GPUs
  - Classify via PCI class; enrich via `nvidia-smi` (driver_version, vbios_version, serial/uuid, vram), `rocm-smi` (driver_version, fw_* firmware versions, vbios_version, serial/unique-id/guid when present)
  - Note: VMs/VFs often omit serial/firmware; cards will show basic PCI info only
- Viewer
  - Card-based nodes with portrait-friendly layouts (Radial/Compact/Hierarchy)
  - Double-click zoom to node/edge target (60% viewport)
  - Toolbar grouping and pixel-perfect alignment
  - Fit and Reset; save/revert removed pending redesign

### Memory (enhance)
- Collect: `dmidecode -t memory`, `lsmem`, `numactl --hardware`, `/sys/devices/system/node`
- Properties: `slot`, `size_gb`, `type`, `speed`, `channel` (best-effort), `rank`, `ecc`, `manufacturer`, `part_number`, `serial`, `numa_node`

### PCI devices (enhance)
- Collect: add `/sys/bus/pci/devices/*` for NUMA, IOMMU group, SR-IOV VF counts
- Properties: `numa_node`, `iommu_group`, `sriov_numvfs`
- Optional: build a proper hierarchy (controllers → functions → endpoints) from `lspci -t`

### GPUs (enhance)
- Driver fallback: record `Driver` from `lspci -Dvmmnnk` when vendor tools absent
- Utilization (deep):
  - NVIDIA: include `utilization.gpu` in `nvidia-smi --query-gpu`
  - AMD: parse utilization from `rocm-smi --json` if available
- Group PF/VF and multi-function devices; link VFs to PF
- Map DRM render nodes: `/sys/class/drm/card*/device` → add `drm_card`
- Normalize units: `temperature_c`, `power_w`, `vram_mb`
- Timeouts: short vendor-tool timeouts to keep scans snappy

### Storage (enhance)
- NVMe details (deep): `nvme id-ctrl/ns`, health/SMART summaries; partition mapping
- SATA/SCSI: optional `smartctl` summaries; link `lsblk` tree edges

### Network (enhance)
- Add PCI mapping from `ethtool -i bus-info` to related PCI node
- Optional stats (deep): `ethtool -S` key counters

### USB (enhance)
- Topology from `lsusb -t`; per-port speed and hubs

### Viewer (next)
- Details panel: a right-side or modal panel that shows all properties for the selected node (full key/value list), including firmware keys like `fw_*`, `vbios_version`, and IDs (serial/uuid). Keeps cards concise while exposing everything.
- Optional: copy-to-clipboard for fields; search/filter nodes by property

### CPU (enhance)
- Enrich from `lscpu -J`: `vendor_id`, `family`, `model`, `stepping`, `min_mhz`, `max_mhz`, cache sizes (L1d/L1i/L2/L3), virtualization
- Parse `numactl --hardware` or `/sys/devices/system/node/node*` for NUMA nodes (cpulist, memory per node); add NUMA cards linked under CPU
- Optional (deep): average current MHz and package temp via `/proc/cpuinfo`, `lm-sensors`, or `/sys/class/thermal/*`

### CLI behavior
- Default: lightweight scan (no root). Gracefully skip missing tools
- `--deep`: enable vendor/privileged tools; enforce per-tool timeouts
- Output: same normalized JSON (`nodes`, `edges`), enriched properties

### Optional packages (by feature)
- Core/buses: `pciutils` (`lspci`), `usbutils` (`lsusb`), `util-linux` (`lscpu`, `lsblk`)
- Memory/NUMA: `dmidecode`, `numactl`
- Storage: `nvme-cli`, `smartmontools`
- Network: `iproute2`, `ethtool`
- GPUs: `nvidia-smi` (NVIDIA), `rocm-smi` (AMD ROCm)
- Sensors (optional): `lm-sensors`

### Notes
- Some tools may require `sudo` (e.g., `dmidecode`, `smartctl`, parts of `ethtool`)
- Prefer JSON outputs where possible; keep parsers tolerant; add timeouts
