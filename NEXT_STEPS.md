### TopoScope: Next Steps

Keep KISS. Default scans stay fast and no-root; deeper details are opt-in.

### Implemented (current)
- CPU: `lscpu -J`
- Memory: total from `/proc/meminfo`; per-DIMM from `dmidecode -t memory` when available
- PCI: structured from `lspci -Dvmmnnk`; link width/speed from `lspci -vv`
- USB: `lsusb`
- Storage: NVMe from `nvme list -o json`; other disks from `lsblk -J`
- Network: interfaces from `ip -br link`, driver/speed from `ethtool`
- GPUs: classified via PCI class; NVIDIA via `nvidia-smi`, AMD via `rocm-smi` (when present)
- Viewer: node “cards” with key details; save/restore view in localStorage

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
