#!/usr/bin/env python3.8
"""
run_buildpy_openfpga.py  —  Pipeline for the buildpy_openfpga image.

Flow (inside Docker container):
  1. build.py               → vpr/arch.xml, vpr/rrg.xml
  1b. patch_vpr_arch        → adds OpenFPGA-required physical io mode
  2. generate_openfpga_arch → openfpga_arch.xml  (pure stdlib, no PRGA)
  3. Yosys synthesis        → syn/result.blif
  3b. activity file         → syn/design.act
  4. OpenFPGA shell         → P&R → link → build → bitstream → RTL

Difference from run_openfpga.py:
  - Same steps and logic; paired with Dockerfile.buildpy_openfpga which
    installs only the PRGA Python package (no PRGA VTR/Yosys/ABC build).
  - arch.xml is the ONLY artefact that crosses PRGA → OpenFPGA.
"""
import os, sys, subprocess, logging, textwrap, re
import xml.etree.ElementTree as ET

logging.basicConfig(level=logging.INFO,
                    format='[%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (container-side)
# ---------------------------------------------------------------------------
FABRIC_DIR   = '/opt/examples/fpga_k4_N1_2x2'
BUILD_PY     = os.path.join(FABRIC_DIR, 'build.py')
GEN_ARCH_PY  = os.path.join(FABRIC_DIR, 'generate_openfpga_arch.py')
APP_V        = os.path.join(FABRIC_DIR, 'app', 'adder', 'src', 'adder.v')
SIM_SETTING  = os.path.join(FABRIC_DIR, 'openfpga_sim_setting.xml')

VPR_ARCH     = os.path.join(FABRIC_DIR, 'vpr', 'arch.xml')
OPENFPGA_ARCH= os.path.join(FABRIC_DIR, 'openfpga_arch.xml')
BLIF_OUT     = os.path.join(FABRIC_DIR, 'syn', 'result.blif')
ACTIVITY_FILE= os.path.join(FABRIC_DIR, 'syn', 'design.act')

OUT_DIR      = os.path.join(FABRIC_DIR, 'openfpga_out')

OPENFPGA_BIN = '/opt/OpenFPGA/build/openfpga/openfpga'
VPR_BIN      = '/opt/OpenFPGA/build/vtr-verilog-to-routing/vpr/vpr'
YOSYS_BIN    = 'yosys'
OPENFPGA_PATH= '/opt/OpenFPGA'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run(cmd, **kw):
    log.info('$ ' + ' '.join(str(c) for c in cmd))
    subprocess.check_call([str(c) for c in cmd], **kw)

def section(title):
    log.info('=' * 60)
    log.info(f'  {title}')
    log.info('=' * 60)

# ---------------------------------------------------------------------------
# Step 1: build.py → vpr/arch.xml
# ---------------------------------------------------------------------------
def step_prga_build():
    section('Step 1: build.py → vpr/arch.xml')

    # If arch.xml is missing but stale pickle exists, remove pickle so PRGA
    # re-runs VPRArchGeneration instead of silently skipping it.
    pkl = os.path.join(FABRIC_DIR, 'ctx.tmp.pkl')
    if not os.path.isfile(VPR_ARCH) and os.path.isfile(pkl):
        log.info('Stale ctx.tmp.pkl found without arch.xml — removing pickle')
        os.remove(pkl)

    run([sys.executable, BUILD_PY], cwd=FABRIC_DIR)

    if not os.path.isfile(VPR_ARCH):
        raise FileNotFoundError(f'build.py did not produce {VPR_ARCH}')
    log.info(f'OK  vpr/arch.xml  ({os.path.getsize(VPR_ARCH)//1024} KB)')

# ---------------------------------------------------------------------------
# Step 1b: patch vpr/arch.xml — add OpenFPGA-required physical io mode
# ---------------------------------------------------------------------------
def step_patch_vpr_arch():
    section('Step 1b: patch vpr/arch.xml — add physical io mode')
    tree = ET.parse(VPR_ARCH)
    root = tree.getroot()

    # Add <model name="io"> to <models> (needed for blif_model=".subckt io")
    models_el = root.find('models')
    if models_el is not None and models_el.find('model[@name="io"]') is None:
        m = ET.SubElement(models_el, 'model')
        m.set('name', 'io')
        ip = ET.SubElement(m, 'input_ports')
        p  = ET.SubElement(ip, 'port')
        p.set('name', 'outpad'); p.set('is_clock', '0')
        op = ET.SubElement(m, 'output_ports')
        p2 = ET.SubElement(op, 'port')
        p2.set('name', 'inpad'); p2.set('is_clock', '0')
        log.info('Added <model name="io"> to <models>')

    # Add physical mode to each io pb_type
    patched = 0
    for io in root.findall('.//pb_type[@name="io"]'):
        if io.find('mode[@name="physical"]') is not None:
            continue
        mode   = ET.SubElement(io, 'mode')
        mode.set('name', 'physical'); mode.set('disable_packing', 'true')
        iopad  = ET.SubElement(mode, 'pb_type')
        iopad.set('name', 'iopad')
        iopad.set('blif_model', '.subckt io')
        iopad.set('num_pb', '1')
        inp_el = ET.SubElement(iopad, 'input')
        inp_el.set('name', 'outpad'); inp_el.set('num_pins', '1')
        out_el = ET.SubElement(iopad, 'output')
        out_el.set('name', 'inpad'); out_el.set('num_pins', '1')
        ic = ET.SubElement(mode, 'interconnect')
        d1 = ET.SubElement(ic, 'direct')
        d1.set('name','outpad'); d1.set('input','io.outpad'); d1.set('output','iopad.outpad')
        d2 = ET.SubElement(ic, 'direct')
        d2.set('name','inpad'); d2.set('input','iopad.inpad'); d2.set('output','io.inpad')
        patched += 1

    if patched:
        tree.write(VPR_ARCH, encoding='unicode', xml_declaration=False)
        log.info(f'Patched {patched} io pb_type(s) with physical mode')
    else:
        log.info('arch.xml already patched — skipping')

# ---------------------------------------------------------------------------
# Step 2: generate_openfpga_arch.py → openfpga_arch.xml
# ---------------------------------------------------------------------------
def step_openfpga_arch():
    section('Step 2: generate_openfpga_arch.py → openfpga_arch.xml')
    run([sys.executable, GEN_ARCH_PY, VPR_ARCH, OPENFPGA_ARCH], cwd=FABRIC_DIR)
    if not os.path.isfile(OPENFPGA_ARCH):
        raise FileNotFoundError(f'generate_openfpga_arch.py did not produce {OPENFPGA_ARCH}')
    log.info(f'OK  openfpga_arch.xml  ({os.path.getsize(OPENFPGA_ARCH)//1024} KB)')

# ---------------------------------------------------------------------------
# Step 3: Yosys synthesis → syn/result.blif
# ---------------------------------------------------------------------------
def step_yosys_synthesis():
    section('Step 3: Yosys synthesis → syn/result.blif')
    os.makedirs(os.path.join(FABRIC_DIR, 'syn'), exist_ok=True)

    synth_script = textwrap.dedent(f"""\
        read_verilog {APP_V}
        synth -top adder
        abc -lut 4
        write_blif -param {BLIF_OUT}
    """)
    run([YOSYS_BIN, '-p', synth_script])

    if not os.path.isfile(BLIF_OUT):
        raise FileNotFoundError(f'Yosys did not produce {BLIF_OUT}')
    log.info(f'OK  syn/result.blif  ({os.path.getsize(BLIF_OUT)} bytes)')

# ---------------------------------------------------------------------------
# Step 3b: generate activity file (required by link_openfpga_arch)
# ---------------------------------------------------------------------------
def step_generate_activity():
    section('Step 3b: generate activity file → syn/design.act')
    signals = []
    with open(BLIF_OUT) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('.inputs') or line.startswith('.outputs'):
                signals.extend(line.split()[1:])
    os.makedirs(os.path.dirname(ACTIVITY_FILE), exist_ok=True)
    with open(ACTIVITY_FILE, 'w') as fh:
        for sig in signals:
            fh.write(f'{sig} 0.5 0.5\n')
    log.info(f'OK  syn/design.act  ({len(signals)} signals)')

# ---------------------------------------------------------------------------
# Step 4: OpenFPGA shell
# ---------------------------------------------------------------------------
def step_openfpga_flow():
    section('Step 4: OpenFPGA shell → P&R → bitstream → fabric RTL')
    os.makedirs(OUT_DIR, exist_ok=True)

    flow_script = textwrap.dedent(f"""\
        vpr {VPR_ARCH} {BLIF_OUT} \\
            --net_file {OUT_DIR}/result.net \\
            --place_file {OUT_DIR}/result.place \\
            --route_file {OUT_DIR}/result.route \\
            --full_stats --nodisp \\
            --route_chan_width 40 \\
            --clock_modeling route

        read_openfpga_arch --file {OPENFPGA_ARCH}

        read_openfpga_simulation_setting \\
            --file {SIM_SETTING}

        link_openfpga_arch --sort_gsb_chan_node_in_edges \\
            --activity_file {ACTIVITY_FILE}

        check_netlist_naming_conflict --fix \\
            --report {OUT_DIR}/naming_conflict_report.txt

        lut_truth_table_fixup

        build_fabric --compress_routing

        repack

        build_architecture_bitstream

        build_fabric_bitstream

        write_fabric_bitstream --format xml \\
            --file {OUT_DIR}/fabric_bitstream.xml

        write_fabric_verilog \\
            --file {OUT_DIR}/rtl \\
            --explicit_port_mapping

        exit
    """)

    flow_file = os.path.join(FABRIC_DIR, 'openfpga_flow_generated.openfpga')
    with open(flow_file, 'w') as fh:
        fh.write(flow_script)

    env = os.environ.copy()
    env['OPENFPGA_PATH'] = OPENFPGA_PATH

    run([OPENFPGA_BIN, '--batch_mode',
         '--interactive_command_file', flow_file],
        cwd=OUT_DIR, env=env)

    log.info('OK  OpenFPGA flow complete')

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    step_prga_build()
    step_patch_vpr_arch()
    step_openfpga_arch()
    step_yosys_synthesis()
    step_generate_activity()
    step_openfpga_flow()

    log.info('')
    log.info('=' * 60)
    log.info('All done!')
    log.info('')
    outputs = {
        'PRGA arch'       : VPR_ARCH,
        'OpenFPGA arch'   : OPENFPGA_ARCH,
        'Synthesis netlist': BLIF_OUT,
        'OpenFPGA P&R net': f'{OUT_DIR}/result.net',
        'Fabric RTL'      : f'{OUT_DIR}/rtl/',
        'Bitstream'       : f'{OUT_DIR}/fabric_bitstream.xml',
    }
    for label, path in outputs.items():
        exists = os.path.exists(path)
        mark   = '✓' if exists else '✗'
        log.info(f'  {mark}  {label:<22}: {os.path.relpath(path, FABRIC_DIR)}')
    log.info('=' * 60)
