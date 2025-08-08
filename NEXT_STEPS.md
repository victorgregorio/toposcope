### TopoScope: Next Steps Plan

Keep KISS. Expand coverage incrementally while preserving fast, no-root defaults.

### Memory (DIMMs)
- Collect: `dmidecode -t memory`, `lsmem`, `numactl --hardware`, `/sys/devices/system/node`, `/proc/meminfo`
- Properties: `slot`, `size_mb`, `type`, `speed_mtps`, `channel` (best-effort), `rank`, `ecc`, `manufacturer`, `part_number`, `serial`, `numa_node`

### PCI devices
- Collect: `lspci -vvnnk`, `lspci -t`, `/sys/bus/pci/devices/*` (driver, NUMA, SR-IOV, IOMMU)
- Properties: `vendor_id`, `device_id`, `class`, `driver`, `link_speed_gbps`, `link_width`, `numa_node`, `sriov_vfs`

### GPUs
- Detect with `lspci`; enrich with `nvidia-smi` (NVML) or `rocm-smi`; fallback to `lspci` only
- Properties: `vendor`, `model`, `driver`, `vram_mb`, `temperature_c`, `power_w`, `pcie_link_speed_gbps`, `pcie_link_width`

### Storage
- NVMe: `nvme list -o json`, `nvme id-ctrl/ns`, `/sys/block/nvme*`
  - Properties: `model`, `firmware`, `namespaces`, `smart_status`
- SATA/SCSI: `lsblk -J`, udev (`ID_MODEL`); optional `smartctl` for SMART

### Network
- Collect: `ip -br link`, `ethtool -i/-S`, `lspci` mapping
- Properties: `ifname`, `driver`, `speed_mbps`, `mac`, `pci_address`

### USB
- Collect: `lsusb -v`, `lsusb -t`, `/sys/bus/usb/devices`
- Properties: `bus`, `port_path`, `speed_mbps`, `class/subclass`, `manufacturer`, `product`, `serial`

### CLI behavior
- Default: lightweight scan (no root, fast). Gracefully skip missing tools
- `--deep`: run privileged/vendor tools where available; add timeouts
- Output: same normalized JSON (`nodes`, `edges`), enriched properties

### Optional packages (by feature)
- Core/buses: `pciutils` (`lspci`), `usbutils` (`lsusb`), `util-linux` (`lscpu`)
- Memory/NUMA: `dmidecode`, `numactl`
- Storage: `nvme-cli`, `smartmontools`, `lsblk` (util-linux)
- Network: `iproute2`, `ethtool`
- GPUs: `nvidia-smi` (NVIDIA drivers), `rocm-smi` (AMD ROCm)
- Sensors (optional): `lm-sensors`

### Notes
- Some tools may require `sudo` (e.g., `dmidecode`, `smartctl`, parts of `ethtool`)
- Keep parsers tolerant; prefer JSON outputs where possible; enforce timeouts
