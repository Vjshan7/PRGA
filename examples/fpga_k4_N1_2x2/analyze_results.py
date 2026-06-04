#!/usr/bin/env python3.8
"""
analyze_results.py  —  Interactive viewer for PRGA + OpenFPGA build outputs.

Displays placement grid, routing nets, logic truth tables, timing analysis,
bitstream statistics, RTL module tree, and Yosys netlist schematic.

Usage (inside the Docker container):
    python3.8 /opt/examples/fpga_k4_N1_2x2/analyze_results.py [SECTION]

SECTION can be: all  placement  routing  logic  timing  bitstream  rtl  schematic
Default        : all
"""
import os, sys, re, subprocess, shutil
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths (container-side)
# ---------------------------------------------------------------------------
BASE     = '/opt/examples/fpga_k4_N1_2x2'
OUT_DIR  = os.path.join(BASE, 'openfpga_out')
SYN_DIR  = os.path.join(BASE, 'syn')
VPR_ARCH = os.path.join(BASE, 'vpr', 'arch.xml')
BLIF     = os.path.join(SYN_DIR, 'result.blif')
PLACE    = os.path.join(OUT_DIR, 'result.place')
ROUTE    = os.path.join(OUT_DIR, 'result.route')
NET      = os.path.join(OUT_DIR, 'result.net')
BSTREAM  = os.path.join(OUT_DIR, 'fabric_bitstream.xml')
RTL_DIR  = os.path.join(OUT_DIR, 'rtl')
VPR_BIN  = '/opt/prga/local/bin/vpr'

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
R   = '\033[91m';  G   = '\033[92m';  Y   = '\033[93m'
B   = '\033[94m';  M   = '\033[95m';  C   = '\033[96m'
W   = '\033[1;97m';  D  = '\033[2m';  RST = '\033[0m'
HD  = '\033[1;7m'

W70 = 72

def hdr(title):
    print(f'\n{HD}  {title}  {RST}')

def sep(c='─', w=W70):
    print(D + c * w + RST)

def missing(path, label='file'):
    print(f'\n  {R}[MISSING]{RST} {label}: {path}')
    return False

def check(path, label=''):
    if not os.path.isfile(path):
        missing(path, label or path)
        return False
    return True

# ---------------------------------------------------------------------------
# SECTION: Placement
# ---------------------------------------------------------------------------

def show_placement():
    hdr('PLACEMENT  ─  4×4 FPGA Floorplan')
    if not check(PLACE, 'result.place'): return

    blocks = defaultdict(list)
    with open(PLACE) as f:
        for line in f:
            if line.startswith('#') or not line.strip(): continue
            if line.startswith('Netlist') or line.startswith('Array'): continue
            p = line.split()
            if len(p) >= 5:
                name, x, y = p[0], int(p[1]), int(p[2])
                blocks[(x, y)].append(name)

    GRID = 4
    CW   = 15

    def cell(x, y):
        items = blocks.get((x, y), [])
        if not items:
            on_edge = (x == 0 or x == GRID or y == 0 or y == GRID)
            return D + f'{"[IO]":^{CW}}' + RST if on_edge else D + f'{"·":^{CW}}' + RST
        label = items[0][:CW-2]
        if label.startswith('out:'):
            return G + f'{label:^{CW}}' + RST
        if re.match(r'^[ab]\[', label):
            return C + f'{label:^{CW}}' + RST
        return Y + f'{label:^{CW}}' + RST

    print()
    print(' ' * 5 + ''.join(f'{x:^{CW+1}}' for x in range(GRID + 1)))
    sep()
    for y in range(GRID, -1, -1):
        row = f'  {y:2} │'
        for x in range(GRID + 1):
            row += ' ' + cell(x, y)
        print(row)
    sep()

    print(f'\n  Legend:  {C}■ Input{RST}   {G}■ Output{RST}   {Y}■ Logic{RST}   {D}■ Empty / IO shell{RST}\n')
    print(f'  Placed blocks ({len(blocks)} locations):')
    for (x, y), names in sorted(blocks.items()):
        print(f'    ({x},{y})  →  {", ".join(names)}')

# ---------------------------------------------------------------------------
# SECTION: Routing
# ---------------------------------------------------------------------------

def show_routing():
    hdr('ROUTING  ─  Net-by-Net Trace')
    if not check(ROUTE, 'result.route'): return

    nets = []
    cur = None
    node_counts = defaultdict(int)

    with open(ROUTE) as f:
        for line in f:
            m = re.match(r'^Net (\d+) \((.+)\)', line)
            if m:
                if cur: nets.append(cur)
                cur = {'id': int(m.group(1)), 'name': m.group(2), 'nodes': []}
            elif cur and 'Node:' in line:
                parts = line.split()
                if len(parts) >= 3:
                    ntype = parts[2]
                    cur['nodes'].append(ntype)
                    node_counts[ntype] += 1
        if cur: nets.append(cur)

    total_segs = node_counts.get('CHANX', 0) + node_counts.get('CHANY', 0)
    print(f'\n  Nets: {W}{len(nets)}{RST}   Routing segments: {W}{total_segs}{RST}')
    sep('·')

    for net in nets:
        segs  = sum(1 for t in net['nodes'] if t in ('CHANX','CHANY'))
        color = G if net['name'].startswith('out:') else \
                (C if re.match(r'^[ab]\[', net['name']) else Y)
        path  = ' → '.join(dict.fromkeys(net['nodes']))
        print(f'  Net {net["id"]:2d}  {color}{net["name"]:<20}{RST}  '
              f'{len(net["nodes"]):3d} nodes  {B}{segs:2d} wires{RST}  '
              f'{D}[{path}]{RST}')

    sep()
    print(f'\n  Routing resource usage:')
    for ntype, cnt in sorted(node_counts.items(), key=lambda x: -x[1]):
        bar = G + '█' * min(cnt, 45) + RST
        print(f'    {ntype:<8}  {cnt:4d}  {bar}')

# ---------------------------------------------------------------------------
# SECTION: Logic (BLIF truth tables)
# ---------------------------------------------------------------------------

def show_logic():
    hdr('LOGIC  ─  Synthesised Netlist (Yosys BLIF)')
    if not check(BLIF, 'result.blif'): return

    with open(BLIF) as f:
        content = f.read()

    m_in  = re.search(r'\.inputs (.+)',  content)
    m_out = re.search(r'\.outputs (.+)', content)
    print(f'\n  {C}Inputs  :{RST}  {m_in.group(1) if m_in else "—"}')
    print(f'  {G}Outputs :{RST}  {m_out.group(1) if m_out else "—"}')

    # Parse LUTs
    luts, cur = [], None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('.names'):
            tokens = line.split()[1:]
            cur = {'inputs': tokens[:-1], 'output': tokens[-1], 'rows': []}
        elif cur and re.match(r'^[01\-]+\s+[01]', line):
            cur['rows'].append(line)
        elif cur:
            if cur['rows']: luts.append(cur)
            cur = None
    if cur and cur['rows']: luts.append(cur)

    print(f'  {Y}LUT count:{RST}  {len(luts)}')
    sep('·')

    for i, lut in enumerate(luts):
        k = len(lut['inputs'])
        print(f'\n  {Y}LUT-{i}  (K={k}){RST}  →  output: {W}{lut["output"]}{RST}')
        hdrs = '  '.join(f'{x[:8]:>8}' for x in lut['inputs'])
        print(f'    {D}{hdrs}   out{RST}')
        sep('·', 50)
        for row in lut['rows']:
            parts  = row.split()
            in_str = '  '.join(f'{c:>8}' for c in parts[0])
            val    = parts[1] if len(parts) > 1 else '?'
            color  = G if val == '1' else D
            print(f'    {in_str}   {color}{val}{RST}')

    sep()
    # ASCII cone diagram
    print(f'\n  Signal cone (BLIF dependency order):')
    deps = {}
    for lut in luts:
        deps[lut['output']] = lut['inputs']
    m_out_sigs = m_out.group(1).split() if m_out else []
    for sig in m_out_sigs:
        if sig in deps:
            print(f'    {G}{sig}{RST}')
            for inp in deps[sig]:
                color = C if inp in (m_in.group(1).split() if m_in else []) else Y
                print(f'      └─ {color}{inp}{RST}')

# ---------------------------------------------------------------------------
# SECTION: Timing (VPR --analysis)
# ---------------------------------------------------------------------------

def show_timing():
    hdr('TIMING  ─  VPR Static Timing Analysis')

    if not os.path.isfile(VPR_BIN):
        print(f'\n  {R}VPR binary not found at {VPR_BIN}{RST}')
        return
    for f in (VPR_ARCH, BLIF, PLACE, ROUTE, NET):
        if not os.path.isfile(f):
            missing(f); return

    rpt_prefix = os.path.join(OUT_DIR, 'timing_')
    print(f'\n  Running VPR --analysis to compute timing ...')
    print(f'  {D}(reports → {rpt_prefix}*.rpt){RST}\n')

    cmd = [
        VPR_BIN, VPR_ARCH, BLIF,
        '--net_file',   NET,
        '--place_file', PLACE,
        '--route_file', ROUTE,
        '--analysis',
        '--timing_report_detail', 'detailed',
        '--outfile_prefix', rpt_prefix,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             cwd=OUT_DIR)
        stdout = res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        print(f'  {R}VPR timed out (120 s){RST}')
        return
    except Exception as e:
        print(f'  {R}Error running VPR: {e}{RST}')
        return

    # Extract key timing lines from VPR stdout
    important = []
    for line in stdout.splitlines():
        lo = line.lower()
        if any(kw in lo for kw in ('critical', 'slack', 'timing', 'fmax',
                                    'wns', 'tns', 'hold', 'setup', 'cpd',
                                    'clock period', 'required', 'delay')):
            important.append(line)

    if important:
        for line in important[:60]:
            color = R if 'fail' in line.lower() or 'negative' in line.lower() \
                    else (G if 'pass' in line.lower() or 'met' in line.lower() else D)
            print(f'  {color}{line}{RST}')
    else:
        # Print last 40 lines of VPR output
        for line in stdout.splitlines()[-40:]:
            print(f'  {D}{line}{RST}')

    # Show generated report files
    sep('·')
    print(f'\n  Generated timing reports:')
    for fname in sorted(os.listdir(OUT_DIR)):
        if fname.endswith('.rpt'):
            fpath = os.path.join(OUT_DIR, fname)
            lc = sum(1 for _ in open(fpath))
            print(f'    {G}{fname}{RST}  ({lc} lines)')
            # Print first meaningful lines of each report
            with open(fpath) as rf:
                for i, line in enumerate(rf):
                    if i > 30: break
                    print(f'      {D}{line.rstrip()}{RST}')
            print()

# ---------------------------------------------------------------------------
# SECTION: Bitstream
# ---------------------------------------------------------------------------

def show_bitstream():
    hdr('BITSTREAM  ─  Frame-Based Configuration')
    if not check(BSTREAM, 'fabric_bitstream.xml'): return

    with open(BSTREAM) as f:
        lines = f.readlines()

    for line in lines[:8]:
        if line.startswith('//'):
            print(f'  {D}{line.rstrip()}{RST}')

    data = [l.strip() for l in lines if re.match(r'^[01]+', l.strip())]
    if not data:
        print(f'\n  {R}No bitstream data found{RST}'); return

    addr_w = len(data[0]) - 1
    ones   = sum(1 for l in data if l[-1] == '1')
    total  = len(data)
    fill   = ones / total * 100
    bar_w  = 44
    g_bar  = int(fill / 100 * bar_w)

    print(f'\n  {Y}Frame count    :{RST}  {total}')
    print(f'  {Y}Address width  :{RST}  {addr_w} bits  →  up to {2**addr_w} locations')
    print(f'  {Y}Data bit/frame :{RST}  1')
    print(f'  {Y}Total bits     :{RST}  {total}')
    print(f'\n  Fill rate:  {G}{"█" * g_bar}{D}{"░" * (bar_w - g_bar)}{RST}'
          f'  {G}{ones} set{RST} / {D}{total - ones} clear{RST}  ({fill:.1f}%)')

    sep('·')
    print(f'\n  First 6 frames:')
    for row in data[:6]:
        color = G if row[-1] == '1' else D
        print(f'    addr={M}{row[:-1]}{RST}  data={color}{row[-1]}{RST}')
    print(f'    {D}... ({total - 9} frames omitted) ...{RST}')
    print(f'  Last 3 frames:')
    for row in data[-3:]:
        color = G if row[-1] == '1' else D
        print(f'    addr={M}{row[:-1]}{RST}  data={color}{row[-1]}{RST}')

# ---------------------------------------------------------------------------
# SECTION: RTL
# ---------------------------------------------------------------------------

def show_rtl():
    hdr('RTL  ─  OpenFPGA-Generated Fabric Verilog')

    total_lines = 0
    sections    = {}
    for root_d, dirs, files in os.walk(RTL_DIR):
        dirs.sort()
        for fname in sorted(files):
            if not fname.endswith('.v'): continue
            fpath = os.path.join(root_d, fname)
            rel   = os.path.relpath(fpath, RTL_DIR)
            sec   = rel.split(os.sep)[0] if os.sep in rel else '(top)'
            with open(fpath) as f:
                lc = sum(1 for _ in f)
            sections.setdefault(sec, []).append((fname, lc, fpath))
            total_lines += lc

    total_files = sum(len(v) for v in sections.values())
    print(f'\n  {W}{total_files} Verilog files{RST}   {W}{total_lines:,} total lines{RST}')
    sep('·')

    colors = {'(top)': W, 'lb': Y, 'routing': B, 'sub_module': M}
    for sec, files in sorted(sections.items()):
        sc      = colors.get(sec, C)
        sec_lns = sum(lc for _, lc, _ in files)
        print(f'\n  {sc}{sec}/{RST}  —  {len(files)} files, {sec_lns} lines')
        for fname, lc, fpath in files:
            # Count modules in file
            with open(fpath) as f:
                content = f.read()
            mods = re.findall(r'\bmodule\s+(\w+)', content)
            bar  = D + '█' * (lc // 25) + RST
            mstr = f'  {D}[{", ".join(mods[:2])}{"..." if len(mods)>2 else ""}]{RST}' \
                   if mods else ''
            print(f'    {fname:<62}  {lc:5} lines  {bar}{mstr}')

    sep()
    top_v = os.path.join(RTL_DIR, 'fpga_top.v')
    if os.path.isfile(top_v):
        with open(top_v) as f:
            content = f.read()
        mods = re.findall(r'\bmodule\s+(\w+)', content)
        print(f'\n  {W}fpga_top.v instantiated modules:{RST}')
        insts = re.findall(r'^\s+(\w+)\s+\w+\s*\(', content, re.MULTILINE)
        for inst in dict.fromkeys(insts):
            print(f'    {G}{inst}{RST}')

# ---------------------------------------------------------------------------
# SECTION: Schematic (Yosys → DOT/SVG)
# ---------------------------------------------------------------------------

def show_schematic():
    hdr('SCHEMATIC  ─  Yosys Netlist Graph')

    if not shutil.which('yosys'):
        print(f'\n  {R}yosys not in PATH — skipping{RST}')
        return
    if not check(BLIF, 'result.blif'): return

    dot_prefix = '/tmp/fpga_schematic'
    dot_file   = dot_prefix + '.dot'
    svg_file   = dot_prefix + '.svg'

    print(f'\n  Running yosys show ...')
    cmd = ['yosys', '-p',
           f'read_blif {BLIF}; '
           f'show -format dot -prefix {dot_prefix}']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print(f'  {R}yosys timed out{RST}'); return

    if not os.path.isfile(dot_file):
        print(f'  {R}DOT file not generated{RST}')
        if r.stderr:
            for l in r.stderr.splitlines()[-10:]:
                print(f'  {D}{l}{RST}')
        return

    print(f'  {G}DOT file:{RST} {dot_file}')
    with open(dot_file) as f:
        dot_lines = f.readlines()
    print(f'  ({len(dot_lines)} lines)')
    sep('·')
    for line in dot_lines[:50]:
        print(f'  {D}{line.rstrip()}{RST}')
    if len(dot_lines) > 50:
        print(f'  {D}... ({len(dot_lines)-50} more lines){RST}')

    # Convert to SVG if graphviz is available
    if shutil.which('dot'):
        subprocess.run(['dot', '-Tsvg', dot_file, '-o', svg_file],
                       capture_output=True, timeout=20)
        if os.path.isfile(svg_file):
            sz = os.path.getsize(svg_file)
            print(f'\n  {G}SVG schematic:{RST} {svg_file}  ({sz:,} bytes)')
            print(f'  To copy to host (from another terminal):')
            print(f'  {Y}  docker cp $(docker ps -lq):{svg_file} ./fpga_schematic.svg{RST}')
    else:
        print(f'\n  {D}(graphviz not installed — SVG conversion skipped){RST}')
        print(f'  Install: apt-get install -y graphviz')

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SECTIONS = {
    'placement': show_placement,
    'routing':   show_routing,
    'logic':     show_logic,
    'timing':    show_timing,
    'bitstream': show_bitstream,
    'rtl':       show_rtl,
    'schematic': show_schematic,
}

if __name__ == '__main__':
    section = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if section == 'all':
        for fn in SECTIONS.values():
            fn()
    elif section in SECTIONS:
        SECTIONS[section]()
    else:
        print(f'Unknown section "{section}". Choose: all, {", ".join(SECTIONS)}')
        sys.exit(1)
    print()
