"""
generate_openfpga_arch.py
Generates openfpga_arch.xml from PRGA's vpr/arch.xml.

This bridges PRGA's Python-defined fabric to OpenFPGA's XML-based
circuit-level flow.  It reads switch/segment names from arch.xml so
the output is always consistent with what PRGA generated.

Usage (standalone):
    python3.8 generate_openfpga_arch.py vpr/arch.xml openfpga_arch.xml

Usage (from build.py):
    from generate_openfpga_arch import generate_openfpga_arch_xml
    generate_openfpga_arch_xml('vpr/arch.xml', 'openfpga_arch.xml', lut_size=4)
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import sys
import os


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sub(parent, tag, **attrib):
    """Append a child element with the given attributes."""
    el = ET.SubElement(parent, tag)
    for k, v in attrib.items():
        el.set(k.rstrip('_'), str(v))
    return el


def _pretty(root):
    """Return indented XML string."""
    raw = ET.tostring(root, encoding='unicode')
    return minidom.parseString(raw).toprettyxml(indent='  ')


# ---------------------------------------------------------------------------
# Parse PRGA's arch.xml to collect names
# ---------------------------------------------------------------------------

def _parse_prga_arch(arch_xml):
    """Return (segments, switches, pb_interconnects) found in arch.xml."""
    tree = ET.parse(arch_xml)
    root = tree.getroot()

    segments = [s.get('name') for s in root.findall('.//segment')]
    switches  = [s.get('name') for s in root.findall('.//switch')]

    # Collect only pb_type interconnect mux names (those inside <interconnect>).
    # root.findall('.//mux') also picks up routing switch buffer definitions
    # like <mux name="default"/> which must be excluded.
    muxes = [m.get('name') for m in root.findall('.//interconnect/mux')]

    return segments, switches, muxes


# ---------------------------------------------------------------------------
# Technology library (required first child of openfpga_architecture)
# Uses PTM 45nm models bundled with OpenFPGA.
# ---------------------------------------------------------------------------

def _add_technology_library(root):
    tl = _sub(root, 'technology_library')
    dl = _sub(tl, 'device_library')

    # NMOS transistor
    nmos = _sub(dl, 'device_model', name='nmos', type='transistor')
    _sub(nmos, 'lib', type='industry', corner='typical', ref='M',
         path='${OPENFPGA_PATH}/openfpga_flow/tech/PTM_45nm/45nm.pm')
    _sub(nmos, 'design', vdd='0.9', pn_ratio='2', minw_ptran='0.1e-6')
    _sub(nmos, 'pmos', name='pmos', chan_length='45e-9', min_width='1.3e-10',
         variation='default_nmos_pmos_variation')
    _sub(nmos, 'nmos', name='nmos', chan_length='45e-9', min_width='1.3e-10',
         variation='default_nmos_pmos_variation')

    # PMOS transistor
    pmos = _sub(dl, 'device_model', name='pmos', type='transistor')
    _sub(pmos, 'lib', type='industry', corner='typical', ref='M',
         path='${OPENFPGA_PATH}/openfpga_flow/tech/PTM_45nm/45nm.pm')
    _sub(pmos, 'design', vdd='0.9', pn_ratio='2', minw_ptran='0.1e-6')
    _sub(pmos, 'pmos', name='pmos', chan_length='45e-9', min_width='1.3e-10',
         variation='default_nmos_pmos_variation')
    _sub(pmos, 'nmos', name='nmos', chan_length='45e-9', min_width='1.3e-10',
         variation='default_nmos_pmos_variation')

    # Variation library
    vl = _sub(tl, 'variation_library')
    _sub(vl, 'variation', name='default_nmos_pmos_variation',
         abs_deviation='0.1', num_sigma='3')


# ---------------------------------------------------------------------------
# Circuit library builders
# ---------------------------------------------------------------------------

def _add_circuit_library(root):
    lib = _sub(root, 'circuit_library')

    # ── Inverter / buffer ───────────────────────────────────────────────────
    inv = _sub(lib, 'circuit_model', type='inv_buf', name='INVTX1',
               prefix='INVTX1', is_default='true')
    _sub(inv, 'design_technology', type='cmos', topology='inverter', size='1')
    _sub(inv, 'input_buffer',  exist='false')
    _sub(inv, 'output_buffer', exist='false')
    _sub(inv, 'port', type='input',  prefix='in',  size='1')
    _sub(inv, 'port', type='output', prefix='out', size='1')

    buf = _sub(lib, 'circuit_model', type='inv_buf', name='BUF2X',
               prefix='BUF2X', is_default='false')
    _sub(buf, 'design_technology', type='cmos', topology='buffer', size='2')
    _sub(buf, 'input_buffer',  exist='false')
    _sub(buf, 'output_buffer', exist='false')
    _sub(buf, 'port', type='input',  prefix='in',  size='1')
    _sub(buf, 'port', type='output', prefix='out', size='1')

    # ── Transmission gate ───────────────────────────────────────────────────
    tg = _sub(lib, 'circuit_model', type='pass_gate', name='TGATE',
              prefix='TGATE', is_default='true')
    _sub(tg, 'design_technology', type='cmos',
         topology='transmission_gate', nmos_size='1', pmos_size='2')
    _sub(tg, 'input_buffer',  exist='false')
    _sub(tg, 'output_buffer', exist='false')
    _sub(tg, 'port', type='input',  prefix='in',   size='1')
    _sub(tg, 'port', type='input',  prefix='sel',  size='1')
    _sub(tg, 'port', type='input',  prefix='selb', size='1')
    _sub(tg, 'port', type='output', prefix='out',  size='1')

    # ── Channel wire (routing segment) ──────────────────────────────────────
    cw = _sub(lib, 'circuit_model', type='chan_wire', name='chan_segment',
              prefix='chan_segment', is_default='true')
    _sub(cw, 'design_technology', type='cmos')
    _sub(cw, 'input_buffer',  exist='false')
    _sub(cw, 'output_buffer', exist='false')
    _sub(cw, 'wire_param', model_type='pi', R='0', C='0', num_level='1')
    _sub(cw, 'port', type='input',  prefix='in',  size='1')
    _sub(cw, 'port', type='output', prefix='out', size='1')

    # ── Direct wire (local interconnect) ────────────────────────────────────
    dw = _sub(lib, 'circuit_model', type='wire', name='direct_interc',
              prefix='direct_interc', is_default='true')
    _sub(dw, 'design_technology', type='cmos')
    _sub(dw, 'input_buffer',  exist='false')
    _sub(dw, 'output_buffer', exist='false')
    _sub(dw, 'wire_param', model_type='pi', R='0', C='0', num_level='1')
    _sub(dw, 'port', type='input',  prefix='in',  size='1')
    _sub(dw, 'port', type='output', prefix='out', size='1')

    # ── Routing multiplexer (tree topology) ─────────────────────────────────
    mux = _sub(lib, 'circuit_model', type='mux', name='mux_tree',
               prefix='mux_tree', is_default='true')
    _sub(mux, 'design_technology', type='cmos', structure='tree')
    _sub(mux, 'input_buffer',  exist='true', circuit_model_name='INVTX1')
    _sub(mux, 'output_buffer', exist='true', circuit_model_name='BUF2X')
    _sub(mux, 'pass_gate_logic', circuit_model_name='TGATE')
    _sub(mux, 'port', type='input',  prefix='in',   size='1')
    _sub(mux, 'port', type='output', prefix='out',  size='1')
    _sub(mux, 'port', type='sram',   prefix='sram', size='1',
         circuit_model_name='SRAM_cell')

    # ── SRAM cell (frame-based) ───────────────────────────────────────────────
    # Frame-based mode requires an external Verilog netlist for the SRAM cell;
    # the module manager cannot synthesise it from port definitions alone.
    # We use OpenFPGA's bundled LATCH module (prefix must match the module name).
    sram = _sub(lib, 'circuit_model', type='sram', name='SRAM_cell',
                prefix='LATCH', is_default='true',
                verilog_netlist='${OPENFPGA_PATH}/openfpga_flow/openfpga_cell_library/verilog/latch.v')
    _sub(sram, 'design_technology', type='cmos')
    _sub(sram, 'input_buffer',  exist='true', circuit_model_name='INVTX1')
    _sub(sram, 'output_buffer', exist='true', circuit_model_name='INVTX1')
    _sub(sram, 'port', type='bl',     prefix='bl',   lib_name='D',  size='1')
    _sub(sram, 'port', type='wl',     prefix='wl',   lib_name='WE', size='1')
    _sub(sram, 'port', type='output', prefix='out',  lib_name='Q',  size='1')
    _sub(sram, 'port', type='output', prefix='outb', lib_name='QN', size='1')

    # ── K4 LUT ───────────────────────────────────────────────────────────────
    # 2^4 = 16 SRAM bits per LUT
    lut = _sub(lib, 'circuit_model', type='lut', name='lut4',
               prefix='lut4', is_default='true')
    _sub(lut, 'design_technology', type='cmos')
    _sub(lut, 'input_buffer',       exist='true', circuit_model_name='INVTX1')
    _sub(lut, 'output_buffer',      exist='true', circuit_model_name='BUF2X')
    _sub(lut, 'pass_gate_logic',    circuit_model_name='TGATE')
    _sub(lut, 'lut_input_inverter', exist='true', circuit_model_name='INVTX1')
    _sub(lut, 'lut_input_buffer',   exist='true', circuit_model_name='BUF2X')
    _sub(lut, 'port', type='input',  prefix='in',   size='4')
    _sub(lut, 'port', type='output', prefix='out',  size='1')
    _sub(lut, 'port', type='sram',   prefix='sram', size='16',
         circuit_model_name='SRAM_cell', default_val='0')

    # ── D flip-flop ──────────────────────────────────────────────────────────
    # Uses OpenFPGA's bundled DFF module (ports: CK, D, Q, QN).
    ff = _sub(lib, 'circuit_model', type='ff', name='dff',
              prefix='DFF', is_default='true',
              verilog_netlist='${OPENFPGA_PATH}/openfpga_flow/openfpga_cell_library/verilog/dff.v')
    _sub(ff, 'design_technology', type='cmos')
    _sub(ff, 'input_buffer',  exist='true', circuit_model_name='INVTX1')
    _sub(ff, 'output_buffer', exist='true', circuit_model_name='INVTX1')
    _sub(ff, 'port', type='input',  prefix='D',   size='1', lib_name='D')
    _sub(ff, 'port', type='output', prefix='Q',   size='1', lib_name='Q')
    _sub(ff, 'port', type='clock',  prefix='clk', size='1', lib_name='CK',
         is_global='true')

    # ── IO pad ───────────────────────────────────────────────────────────────
    # Uses OpenFPGA's bundled GPIO module (ports: A, Y, PAD, DIR).
    # GPIO is a bidir pad controlled by the DIR sram bit (1=input, 0=output).
    io = _sub(lib, 'circuit_model', type='iopad', name='iopad',
              prefix='GPIO', is_default='true',
              verilog_netlist='${OPENFPGA_PATH}/openfpga_flow/openfpga_cell_library/verilog/gpio.v')
    _sub(io, 'design_technology', type='cmos')
    _sub(io, 'input_buffer',  exist='true', circuit_model_name='INVTX1')
    _sub(io, 'output_buffer', exist='true', circuit_model_name='INVTX1')
    _sub(io, 'port', type='inout',  prefix='PAD', size='1',
         is_global='true', is_io='true', is_data_io='true')
    # Mode-select SRAM bit drives GPIO DIR pin (1=input mode, 0=output mode)
    _sub(io, 'port', type='sram',   prefix='DIR', size='1',
         mode_select='true', circuit_model_name='SRAM_cell', default_val='1')
    # FPGA-core-facing ports
    _sub(io, 'port', type='input',  prefix='outpad', lib_name='A', size='1')
    _sub(io, 'port', type='output', prefix='inpad',  lib_name='Y', size='1')

    return lib


# ---------------------------------------------------------------------------
# Configuration protocol
# ---------------------------------------------------------------------------

def _add_config_protocol(root):
    # Frame-based matches PRGA's native programming model
    cp = _sub(root, 'configuration_protocol')
    _sub(cp, 'organization', type='frame_based',
         circuit_model_name='SRAM_cell')


# ---------------------------------------------------------------------------
# Connection block, switch block, routing segments
# ---------------------------------------------------------------------------

def _add_routing(root, segments, switches):
    # Connection block (ipin muxes)
    cb = _sub(root, 'connection_block')
    _sub(cb, 'switch', name='ipin_cblock', circuit_model_name='mux_tree')

    # Switch block — one entry per switch in arch.xml
    sb = _sub(root, 'switch_block')
    for sw in switches:
        _sub(sb, 'switch', name=sw, circuit_model_name='mux_tree')

    # Routing segments
    rs = _sub(root, 'routing_segment')
    for seg in segments:
        _sub(rs, 'segment', name=seg, circuit_model_name='chan_segment')


# ---------------------------------------------------------------------------
# Tile annotations (global clock)
# ---------------------------------------------------------------------------

def _add_tile_annotations(root):
    ta = _sub(root, 'tile_annotations')
    gp = _sub(ta, 'global_port', name='clk', is_clock='true',
              clock_arch_pin_name='clk')
    # PRGA generates tile_clb (physical tile name) at interior positions
    _sub(gp, 'tile', name='tile_clb', port='clk', x='-1', y='-1')


# ---------------------------------------------------------------------------
# pb_type annotations
# Maps PRGA's pb_type hierarchy to physical circuit models.
#
# PRGA arch.xml hierarchy (from inspection):
#   iob -> io -> {io_mode_input, io_mode_output}
#   clb -> cluster -> lut -> lut_lut  (class="lut")
#                  -> ff              (class="flipflop")
# ---------------------------------------------------------------------------

def _add_pb_type_annotations(root, mux_names):
    pa = _sub(root, 'pb_type_annotations')

    # ── IO physical mode: the 'physical' mode (added to vpr/arch.xml by
    # step_patch_vpr_arch) contains a single bidir iopad that both
    # operating modes (mode_input / mode_output) map onto.
    _sub(pa, 'pb_type', name='iob.io', physical_mode_name='physical')

    # ── Physical iopad in physical mode: bind to GPIO circuit model ──────
    # mode_bits='1' declares 1 mode-select SRAM bit (the DIR port in GPIO).
    # Operating modes compare against this width: mode_bits sizes must match.
    iopad_p = _sub(pa, 'pb_type',
                   name='iob.io[physical].iopad',
                   circuit_model_name='iopad',
                   mode_bits='1')
    _sub(iopad_p, 'port', name='outpad', physical_mode_port='outpad',
         physical_mode_pin_rotate_offset='0')
    _sub(iopad_p, 'port', name='inpad', physical_mode_port='inpad',
         physical_mode_pin_rotate_offset='0')

    # ── i_pad (operating, mode_input) → maps to physical iopad ──────────
    # mode_bits='1' → DIR SRAM cell = 1 → GPIO input mode (PAD→Y path active)
    i_pad = _sub(pa, 'pb_type',
                 name='iob.io[mode_input].io_mode_input.i_pad',
                 physical_pb_type_name='iob.io[physical].iopad',
                 mode_bits='1')
    _sub(i_pad, 'port', name='inpad', physical_mode_port='inpad',
         physical_mode_pin_rotate_offset='0')

    # ── o_pad (operating, mode_output) → maps to physical iopad ─────────
    # mode_bits='0' → DIR SRAM cell = 0 → GPIO output mode (A→PAD path active)
    o_pad = _sub(pa, 'pb_type',
                 name='iob.io[mode_output].io_mode_output.o_pad',
                 physical_pb_type_name='iob.io[physical].iopad',
                 mode_bits='0')
    _sub(o_pad, 'port', name='outpad', physical_mode_port='outpad',
         physical_mode_pin_rotate_offset='0')

    # Paths must start from the root logical block (clb or iob).
    # PbParser splits on '.' and uses 'default' mode for each parent without [].
    # Without the full path, try_find_pb_type_with_given_path looks for a
    # top-level logical block with that name and never finds nested pb_types.

    # ── cluster: only the output mux needs a circuit model annotation ────────
    clust = _sub(pa, 'pb_type', name='clb.cluster')
    for mx in mux_names:
        _sub(clust, 'interconnect', name=mx, circuit_model_name='mux_tree')

    # ── Physical LUT: clb → cluster → lut → lut_lut (class=lut) ──────────
    # No mode_bits: lut4 circuit model has no mode selection ports (size=0).
    lut_p = _sub(pa, 'pb_type', name='clb.cluster.lut.lut_lut',
                 circuit_model_name='lut4')
    _sub(lut_p, 'port', name='in',  physical_mode_port='in',
         physical_mode_pin_rotate_offset='0')
    _sub(lut_p, 'port', name='out', physical_mode_port='out')

    # ── Flip-flop: clb → cluster → ff (class=flipflop) ───────────────────
    ff = _sub(pa, 'pb_type', name='clb.cluster.ff',
              circuit_model_name='dff')
    _sub(ff, 'port', name='D',   physical_mode_port='D')
    _sub(ff, 'port', name='Q',   physical_mode_port='Q')
    _sub(ff, 'port', name='clk', physical_mode_port='clk')


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_openfpga_arch_xml(arch_xml, output_path, lut_size=4):
    """
    Read PRGA's arch.xml and emit a companion openfpga_arch.xml.

    Parameters
    ----------
    arch_xml    : path to PRGA-generated vpr/arch.xml
    output_path : where to write openfpga_arch.xml
    lut_size    : K value (default 4)
    """
    segments, switches, mux_names = _parse_prga_arch(arch_xml)
    print(f'[openfpga_arch] segments : {segments}')
    print(f'[openfpga_arch] switches : {switches}')
    print(f'[openfpga_arch] mux interconnects: {mux_names}')

    root = ET.Element('openfpga_architecture')

    _add_technology_library(root)
    _add_circuit_library(root)
    _add_config_protocol(root)
    _add_routing(root, segments, switches)
    _add_pb_type_annotations(root, mux_names)

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    xml_str = _pretty(root)
    with open(output_path, 'w') as f:
        f.write(xml_str)

    print(f'[openfpga_arch] Written → {output_path}')


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: generate_openfpga_arch.py <arch.xml> <openfpga_arch.xml>')
        sys.exit(1)
    generate_openfpga_arch_xml(sys.argv[1], sys.argv[2])
