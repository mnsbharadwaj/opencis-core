#!/usr/bin/env python3
"""
run_pbr_env.py — Single-script PBR test environment.

Launches EVERYTHING in one process — no startup ordering issues:
  • CxlFabricManager  (FM MCTP server on :8100, switch connects here)
  • CxlSwitch         (PBR mode, connects to FM on :8100, devices on :8000)
                       Port 0: USP | Port 1: DSP (SLD) | Port 2: DSP (GFD)
  • SingleLogicalDevice  (1 MiB memory, connects to switch port 1)
  • GenericFabricDevice  (GFD, connects to switch port 2)

Once running, use pbr_fm_cli.py in another terminal to send commands.

Usage
-----
  python run_pbr_env.py
  python run_pbr_env.py --switch-port 8000 --fm-port 8100 --mem-file pbr_dev_mem.bin

OS agnostic: Windows / Linux / macOS.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from opencis.apps.cxl_switch import CxlSwitch, CxlSwitchConfig      # noqa
from opencis.apps.fabric_manager import CxlFabricManager              # noqa
from opencis.apps.single_logical_device import SingleLogicalDevice    # noqa
from opencis.apps.generic_fabric_device import GenericFabricDevice    # noqa
from opencis.cxl.component.physical_port_manager import PortConfig, PORT_TYPE  # noqa

# ── ANSI colours ─────────────────────────────────────────────────────────────
if sys.platform == "win32":
    import os
    os.system("")
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RESET = "\033[0m"

DEFAULT_SLD_MEM_FILE = "pbr_sld_mem.bin"
DEFAULT_GFD_MEM_FILE = "pbr_gfd_mem.bin"
DEFAULT_MEM_SIZE = 1 * 1024 * 1024   # 1 MiB
# Legacy alias kept for backward compat
DEFAULT_MEM_FILE = DEFAULT_SLD_MEM_FILE


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="PBR all-in-one environment (switch + FM + SLD + GFD)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--switch-host", default="0.0.0.0",   help="Switch device listen host")
    p.add_argument("--switch-port", type=int, default=8000, help="Switch TCP port (devices connect here)")
    p.add_argument("--fm-host",     default="0.0.0.0",   help="FM MCTP listen host (switch connects here)")
    p.add_argument("--fm-port",     type=int, default=8100, help="FM MCTP port")
    p.add_argument("--sio-host",    default="0.0.0.0",   help="FM Socket.IO listen host")
    p.add_argument("--sio-port",    type=int, default=8200, help="FM Socket.IO port (CLI connects here)")
    p.add_argument("--sld-mem",     default=DEFAULT_SLD_MEM_FILE, help="SLD (port 1) memory file")
    p.add_argument("--gfd-mem",     default=DEFAULT_GFD_MEM_FILE, help="SLD2/GFD (port 2) memory file")
    p.add_argument("--mem-size",    type=int, default=DEFAULT_MEM_SIZE, help="Memory size per device (bytes)")
    # Legacy alias
    p.add_argument("--mem-file",    default=None, help="Legacy alias for --sld-mem")
    return p.parse_args()


def _prepare_memory_file(path: Path, size: int) -> None:
    if not path.exists():
        path.write_bytes(b"\x00" * size)
        print(f"{YELLOW}[Env] Created memory file: {path.resolve()} ({size:,} bytes){RESET}")
    else:
        existing = path.stat().st_size
        if existing < size:
            with path.open("ab") as f:
                f.write(b"\x00" * (size - existing))


async def _run(args: argparse.Namespace) -> None:
    # Resolve memory file paths (legacy --mem-file overrides --sld-mem)
    sld_mem = Path(args.mem_file if args.mem_file else args.sld_mem)
    gfd_mem = Path(args.gfd_mem)
    _prepare_memory_file(sld_mem, args.mem_size)
    _prepare_memory_file(gfd_mem, args.mem_size)

    print(f"\n{BOLD}{CYAN}{'━' * 62}{RESET}")
    print(f"{BOLD}  PBR Test Environment (all-in-one){RESET}")
    print(f"{CYAN}{'━' * 62}{RESET}")
    print(f"  FM MCTP (switch) : {args.fm_host}:{args.fm_port}   ← switch connects here")
    print(f"  FM Socket.IO     : {args.sio_host}:{args.sio_port}  ← pbr_fm_cli.py connects here")
    print(f"  Switch devices   : {args.switch_host}:{args.switch_port}")
    print(f"    Port 0 : USP")
    print(f"    Port 1 : DSP  ← SLD  (memory: {sld_mem.name})")
    print(f"    Port 2 : DSP  ← SLD2 (memory: {gfd_mem.name})")
    print(f"  SLD memory file  : {sld_mem.resolve()}")
    print(f"  GFD memory file  : {gfd_mem.resolve()}")
    print(f"{CYAN}{'━' * 62}{RESET}")
    print(f"\n{YELLOW}Tip: Run  python pbr_fm_cli.py  in another terminal.{RESET}\n")

    # ── Fabric Manager (MCTP server — switch connects to this) ───────────────
    fm = CxlFabricManager(
        mctp_host=args.fm_host,
        mctp_port=args.fm_port,
        socketio_host=args.sio_host,
        socketio_port=args.sio_port,
    )

    # ── PBR Switch (client → FM, server ← devices) ──────────────────────────
    sw_config = CxlSwitchConfig(
        port_configs=[
            PortConfig(PORT_TYPE.USP),   # port 0 — upstream
            PortConfig(PORT_TYPE.DSP),   # port 1 — SLD connects here
            PortConfig(PORT_TYPE.DSP),   # port 2 — GFD connects here
        ],
        host=args.switch_host,
        port=args.switch_port,
        mctp_host="127.0.0.1",          # switch connects TO FM on loopback
        mctp_port=args.fm_port,
        enable_pbr=True,
        run_as_child=False,
    )
    switch = CxlSwitch(sw_config, device_configs=[])

    # ── SLD port 1 — Type-3 CXL memory device ────────────────────────────────
    sld = SingleLogicalDevice(
        memory_size=args.mem_size,
        memory_file=str(sld_mem),
        serial_number="0000000000000001",
        host="127.0.0.1",
        port=args.switch_port,
        port_index=1,
    )

    # ── SLD port 2 — second memory device (acts as "GFD" target in routing) ──
    # GenericFabricDevice has no memory backing store — use a second SLD so the
    # CLI can do independent write/read verification on both DSP ports.
    sld2 = SingleLogicalDevice(
        memory_size=args.mem_size,
        memory_file=str(gfd_mem),
        serial_number="0000000000000002",
        host="127.0.0.1",
        port=args.switch_port,
        port_index=2,
    )

    # ── Start FM server first so the switch can connect ──────────────────────
    fm_task     = asyncio.create_task(fm.run())
    await fm.wait_for_ready()
    print(f"{GREEN}[Env] FM server ready on :{args.fm_port}{RESET}")

    # ── Start switch — connects to FM, listens for devices ───────────────────
    sw_task     = asyncio.create_task(switch.run())
    await switch.wait_for_ready()
    print(f"{GREEN}[Env] Switch ready on :{args.switch_port}{RESET}")

    # ── Start SLD and SLD2 — connect to switch ───────────────────────────────
    sld_task    = asyncio.create_task(sld.run())
    sld2_task   = asyncio.create_task(sld2.run())
    await sld.wait_for_ready()
    print(f"{GREEN}[Env] SLD  ready (port 1, memory: {sld_mem.name}){RESET}")
    await sld2.wait_for_ready()
    print(f"{GREEN}[Env] SLD2 ready (port 2, memory: {gfd_mem.name}){RESET}")

    print(f"\n{BOLD}{GREEN}All components up.  Run pbr_fm_cli.py to start testing.{RESET}")
    print(f"{CYAN}Press Ctrl+C to stop.{RESET}\n")

    await asyncio.gather(fm_task, sw_task, sld_task, sld2_task)


def main() -> None:
    args = _parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print(f"\n{BOLD}[Env] Stopped.{RESET}")


if __name__ == "__main__":
    main()
