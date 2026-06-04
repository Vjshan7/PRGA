"""
run_openfpga.py
Full PRGA + OpenFPGA pipeline for the 2x2 K4/N1 FPGA + 2-bit adder.

Steps:
  1. PRGA  — build fabric (arch.xml, rrg.xml, syn/, rtl/)
  2. Bridge — generate openfpga_arch.xml from vpr/arch.xml
  3. Yosys — synthesise adder.v → syn/result.blif
  4. OpenFPGA — place & route, fabric RTL, bitstream

Run inside the prga_open_fpga container:
  docker run --rm -v .../examples:/workspace prga_open_fpga:latest \
             python3.8 /workspace/fpga_k4_N1_2x2/run_openfpga.py

Outputs (all under /workspace/fpga_k4_N1_2x2/):
  vpr/arch.xml              PRGA VPR architecture
  vpr/rrg.xml               Routing resource graph
  syn/result.blif           Synthesised BLIF netlist
  openfpga_arch.xml         Circuit-level companion arch
  openfpga_out/rtl/         Fabric Verilog from OpenFPGA
  openfpga_out/fabric_bitstream.xml
"""

import os
import sys
import subprocess
import shutil
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
FABRIC_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE  = os.path.dirname(FABRIC_DIR)          # /workspace
APP_SRC    = os.path.join(WORKSPACE, 'app', 'adder', 'src', 'adder.v')

VPR_ARCH        = os.path.join(FABRIC_DIR, 'vpr', 'arch.xml')
OPENFPGA_ARCH   = os.path.join(FABRIC_DIR, 'openfpga_arch.xml')
OPENFPGA_SIM    = os.path.join(FABRIC_DIR, 'openfpga_sim_setting.xml')
BLIF_OUT        = os.path.join(FABRIC_DIR, 'syn', 'result.blif')
OPENFPGA_SCRIPT  = os.path.join(FABRIC_DIR, 'openfpga_flow.openfpga')
ACTIVITY_FILE    = os.path.join(FABRIC_DIR, 'syn', 'design.act')


def run(cmd, cwd=None, check=True):
    """Run a shell command, stream output, raise on failure."""
    log.info('$ ' + ' '.join(cmd) if isinstance(cmd, list) else cmd)
    result = subprocess.run(
        cmd, cwd=cwd or FABRIC_DIR,
        stdout=None, stderr=None,   # inherit → streamed to terminal
        shell=isinstance(cmd, str),
    )
    if check and result.returncode != 0:
        sys.exit(f'[FAILED] exit code {result.returncode}')
    return result.returncode


def find_yosys():
    """Prefer OpenFPGA's newer Yosys (has synth_openfpga); fall back to PRGA's."""
    openfpga_yosys = '/opt/OpenFPGA/build/yosys/yosys'
    if os.path.isfile(openfpga_yosys):
        log.info(f'Using OpenFPGA Yosys: {openfpga_yosys}')
        return openfpga_yosys
    fallback = shutil.which('yosys') or 'yosys'
    log.info(f'Using fallback Yosys: {fallback}')
    return fallback


# ── Step 1: PRGA fabric build ─────────────────────────────────────────────────
def step_prga_build():
    log.info('=' * 60)
    log.info('Step 1: PRGA — build 2x2 FPGA architecture')
    log.info('=' * 60)

    # Ensure PRGA is importable
    try:
        import prga  # noqa: F401
    except ImportError:
        log.info('PRGA not found — installing from /opt/prga/prga.py ...')
        run([sys.executable, '-m', 'pip', 'install', '-q', '-e', '/opt/prga/prga.py'])
        sys.path.insert(0, '/opt/prga/prga.py')

    # build.py only generates vpr/arch.xml inside the 'except FileNotFoundError'
    # block (i.e., when ctx.tmp.pkl doesn't exist).  If arch.xml is absent but
    # the pickle exists we have a stale state — delete the pickle to force a
    # full rebuild so VPRArchGeneration runs.
    pkl = os.path.join(FABRIC_DIR, 'ctx.tmp.pkl')
    if not os.path.isfile(VPR_ARCH) and os.path.isfile(pkl):
        log.info('ctx.tmp.pkl present but vpr/arch.xml missing — deleting stale pickle')
        os.remove(pkl)

    run([sys.executable, os.path.join(FABRIC_DIR, 'build.py')])

    if not os.path.isfile(VPR_ARCH):
        sys.exit('[ERROR] vpr/arch.xml was not created by build.py')
    log.info('OK  vpr/arch.xml generated')


# ── Step 1b: Patch VPR arch to add 'physical' mode for OpenFPGA ──────────────
def step_patch_vpr_arch():
    """Insert a 'physical' (disable_packing) mode into each io pb_type.

    OpenFPGA requires a single bidir physical primitive that both operating
    modes (mode_input / mode_output) can map to.  PRGA doesn't generate this
    mode, so we add it here before generate_openfpga_arch.py runs.
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(VPR_ARCH)
    root = tree.getroot()
    patched = 0

    # Add 'io' model to <models> section (needed for blif_model='.subckt io')
    models_el = root.find('models')
    if models_el is not None and models_el.find('model[@name="io"]') is None:
        m = ET.SubElement(models_el, 'model')
        m.set('name', 'io')
        ip = ET.SubElement(m, 'input_ports')
        p = ET.SubElement(ip, 'port')
        p.set('name', 'outpad')
        p.set('is_clock', '0')
        op = ET.SubElement(m, 'output_ports')
        p2 = ET.SubElement(op, 'port')
        p2.set('name', 'inpad')
        p2.set('is_clock', '0')

    for io in root.findall('.//pb_type[@name="io"]'):
        if io.find('mode[@name="physical"]') is not None:
            continue  # already patched

        mode = ET.SubElement(io, 'mode')
        mode.set('name', 'physical')
        mode.set('disable_packing', 'true')

        iopad = ET.SubElement(mode, 'pb_type')
        iopad.set('name', 'iopad')
        iopad.set('blif_model', '.subckt io')
        iopad.set('num_pb', '1')
        inp_el = ET.SubElement(iopad, 'input')
        inp_el.set('name', 'outpad')
        inp_el.set('num_pins', '1')

        out_el = ET.SubElement(iopad, 'output')
        out_el.set('name', 'inpad')
        out_el.set('num_pins', '1')

        ic = ET.SubElement(mode, 'interconnect')

        d1 = ET.SubElement(ic, 'direct')
        d1.set('name', 'outpad')
        d1.set('input', 'io.outpad')
        d1.set('output', 'iopad.outpad')

        d2 = ET.SubElement(ic, 'direct')
        d2.set('name', 'inpad')
        d2.set('input', 'iopad.inpad')
        d2.set('output', 'io.inpad')

        patched += 1

    if patched:
        tree.write(VPR_ARCH, encoding='unicode', xml_declaration=False)
        log.info('Patched %d io pb_type(s) with physical mode in %s', patched, VPR_ARCH)
    else:
        log.info('VPR arch already has physical mode — no patch needed')


# ── Step 2: Generate openfpga_arch.xml ───────────────────────────────────────
def step_openfpga_arch():
    log.info('=' * 60)
    log.info('Step 2: Generate openfpga_arch.xml from vpr/arch.xml')
    log.info('=' * 60)

    sys.path.insert(0, FABRIC_DIR)
    from generate_openfpga_arch import generate_openfpga_arch_xml
    generate_openfpga_arch_xml(VPR_ARCH, OPENFPGA_ARCH, lut_size=4)

    if not os.path.isfile(OPENFPGA_ARCH):
        sys.exit('[ERROR] openfpga_arch.xml was not created')
    log.info('OK  openfpga_arch.xml generated')


# ── Step 3: Yosys synthesis ───────────────────────────────────────────────────
def step_yosys_synthesis():
    log.info('=' * 60)
    log.info('Step 3: Yosys synthesis → syn/result.blif')
    log.info('=' * 60)

    yosys = find_yosys()
    synth_tcl = os.path.join(FABRIC_DIR, 'syn', 'run_synth.tcl')

    tcl = f"""\
yosys -import
read_verilog {APP_SRC}
hierarchy -top adder
source {FABRIC_DIR}/syn/read_lib.tcl
source {FABRIC_DIR}/syn/synth.tcl
write_blif -impltf {BLIF_OUT}
"""
    os.makedirs(os.path.join(FABRIC_DIR, 'syn'), exist_ok=True)
    with open(synth_tcl, 'w') as f:
        f.write(tcl)

    run([yosys, synth_tcl])

    if not os.path.isfile(BLIF_OUT):
        sys.exit('[ERROR] syn/result.blif was not created')
    log.info('OK  synthesis complete')


# ── Step 3b: Generate signal activity file from synthesis BLIF ───────────────
def step_generate_activity():
    """Write a flat ACE2-format activity file from the synthesis BLIF.

    link_openfpga_arch requires signal activity data.  For flows without
    real simulation we assign uniform probability/toggle (0.5) to every
    primary I/O.  The ACE2 format is: <net_name> <prob> <toggle_rate>.
    """
    log.info('=' * 60)
    log.info('Step 3b: Generate signal activity file')
    log.info('=' * 60)

    signals = []
    with open(BLIF_OUT) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('.inputs') or line.startswith('.outputs'):
                # Continuation lines end with \ — join them first
                tokens = line.split()[1:]  # drop the keyword
                signals.extend(tokens)

    os.makedirs(os.path.dirname(ACTIVITY_FILE), exist_ok=True)
    with open(ACTIVITY_FILE, 'w') as fh:
        for sig in signals:
            fh.write(f'{sig} 0.5 0.5\n')

    log.info('OK  activity file written (%d signals): %s', len(signals), ACTIVITY_FILE)


# ── Step 4: OpenFPGA P&R + fabric RTL + bitstream ────────────────────────────
def step_openfpga_flow():
    log.info('=' * 60)
    log.info('Step 4: OpenFPGA — P&R + fabric RTL + bitstream')
    log.info('=' * 60)

    out_dir = os.path.join(FABRIC_DIR, 'openfpga_out')
    rtl_dir = os.path.join(out_dir, 'rtl')
    os.makedirs(rtl_dir, exist_ok=True)

    # Generate flow script at runtime so paths are always correct regardless
    # of where the examples directory is mounted inside the container.
    script = f"""\
# OpenFPGA shell script — generated by run_openfpga.py
# Command order matches OpenFPGA v1.2: VPR must run before read_openfpga_arch
# so tile annotations can be validated against the loaded VPR architecture.

# 1. VPR place & route (must come first so arch data is in memory)
vpr {VPR_ARCH} {BLIF_OUT} \\
    --clock_modeling route \\
    --route_chan_width 100 \\
    --allow_unrelated_clustering on \\
    --seed 1 \\
    --net_file   {out_dir}/result.net \\
    --place_file {out_dir}/result.place \\
    --route_file {out_dir}/result.route

# 2. Read OpenFPGA circuit-level arch (validated against loaded VPR arch)
read_openfpga_arch --file {OPENFPGA_ARCH}

# 3. Read simulation settings (required before link_openfpga_arch)
read_openfpga_simulation_setting --file {OPENFPGA_SIM}

# 4. Link circuit models to the routed netlist
link_openfpga_arch --sort_gsb_chan_node_in_edges \\
    --activity_file {ACTIVITY_FILE}

# 5. Fix any naming conflicts introduced by VPR packing
check_netlist_naming_conflict --fix \\
    --report {out_dir}/naming_conflict_report.txt

# 6. Fix up LUT truth tables based on packing results
lut_truth_table_fixup

# 7. Build the programmable fabric netlist
build_fabric --compress_routing

# 8. Repack the netlist to physical pbs (required before bitstream/testbench)
repack

# 9. Build and write bitstream
build_architecture_bitstream
build_fabric_bitstream
write_fabric_bitstream \\
    --file {out_dir}/fabric_bitstream.xml \\
    --format plain_text \\
    --verbose

# 10. Write fabric RTL Verilog
write_fabric_verilog \\
    --file {rtl_dir} \\
    --explicit_port_mapping \\
    --verbose

exit
"""

    script_path = os.path.join(FABRIC_DIR, 'openfpga_flow_generated.openfpga')
    with open(script_path, 'w') as f:
        f.write(script)
    log.info('Generated OpenFPGA script: %s', script_path)

    openfpga_bin = shutil.which('openfpga') or '/opt/OpenFPGA/build/openfpga/openfpga'
    run([openfpga_bin, '-batch', '-f', script_path])
    log.info('OK  OpenFPGA flow complete')


# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary():
    outputs = [
        ('PRGA arch',         'vpr/arch.xml'),
        ('OpenFPGA arch',     'openfpga_arch.xml'),
        ('Synthesis netlist', 'syn/result.blif'),
        ('OpenFPGA P&R net',  'openfpga_out/result.net'),
        ('Fabric RTL',        'openfpga_out/rtl/'),
        ('Bitstream',         'openfpga_out/fabric_bitstream.xml'),
    ]
    log.info('=' * 60)
    log.info('All done!')
    log.info('')
    for label, rel in outputs:
        path = os.path.join(FABRIC_DIR, rel)
        exists = '✓' if os.path.exists(path) else '✗ missing'
        log.info(f'  {label:<22}: {rel}  {exists}')
    log.info('=' * 60)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.chdir(FABRIC_DIR)
    os.makedirs('vpr', exist_ok=True)
    os.makedirs('syn', exist_ok=True)
    os.makedirs('rtl', exist_ok=True)
    os.makedirs('openfpga_out', exist_ok=True)

    step_prga_build()
    step_patch_vpr_arch()
    step_openfpga_arch()
    step_yosys_synthesis()
    step_generate_activity()
    step_openfpga_flow()
    print_summary()
