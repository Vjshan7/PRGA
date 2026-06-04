"""
2x2 FPGA fabric generator for PRGA (Princeton Reconfigurable Gate Array).

Fabric spec:
  - Total array : 4x4  (includes IO perimeter)
  - Interior CLBs: 2x2 → 4 CLBs, each with 1 slice
  - Each slice  : 1× K4 LUT + 1× D flip-flop
  - Total LUT4s : 4
  - Programming : frame-based
  - Routing     : L1 segments (length-1), cycle-free switch boxes

Run inside Docker:
  python3.8 build.py
"""

from prga import *
from itertools import product
import sys
import logging

logging.getLogger("prga").setLevel(logging.DEBUG)

try:
    ctx = Context.unpickle("ctx.tmp.pkl")

except FileNotFoundError:
    ctx = Context()

    # Global clock signal
    gbl_clk = ctx.create_global("clk", is_clock=True)
    gbl_clk.bind((0, 1), 0)

    # Length-1 routing segments
    ctx.create_segment('L1', 20, 1)

    # ----------------------------------------------------------------
    # Slice: 1× LUT4 + 1× FF, LUT output feeds both FF.D and slice out
    # ----------------------------------------------------------------
    builder = ctx.build_slice("slice")
    clk = builder.create_clock("clk")
    i   = builder.create_input("i", 4)
    o   = builder.create_output("o", 1)
    lut = builder.instantiate(ctx.primitives["lut4"], "lut")
    ff  = builder.instantiate(ctx.primitives["flipflop"], "ff")
    builder.connect(clk, ff.pins['clk'])
    builder.connect(i, lut.pins['in'])
    builder.connect(lut.pins['out'], o)
    builder.connect(lut.pins['out'], ff.pins['D'], vpr_pack_patterns=('lut_dff',))
    builder.connect(ff.pins['Q'], o)
    cluster = builder.commit()

    # ----------------------------------------------------------------
    # IOB: bidirectional pad
    # ----------------------------------------------------------------
    builder = ctx.build_io_block("iob")
    o_pad = builder.create_input("outpad", 1)
    i_pad = builder.create_output("inpad", 1)
    builder.connect(builder.instances['io'].pins['inpad'], i_pad)
    builder.connect(o_pad, builder.instances['io'].pins['outpad'])
    iob = builder.commit()

    # ----------------------------------------------------------------
    # CLB: 1 cluster (N=1), inputs from west, output to east
    # ----------------------------------------------------------------
    builder = ctx.build_logic_block("clb")
    clk_port = builder.create_global(gbl_clk, Orientation.south)
    inst = builder.instantiate(cluster, "cluster")
    builder.connect(clk_port, inst.pins['clk'])
    builder.connect(builder.create_input("i", 4, Orientation.west), inst.pins['i'])
    builder.connect(inst.pins['o'], builder.create_output("o", 1, Orientation.east))
    clb = builder.commit()

    # CLB tile (switch box fill fractions tuned for small fabric)
    clbtile = ctx.build_tile(clb).fill((0.4, 0.25)).auto_connect().commit()

    # IO tiles — one per edge orientation, each holding 4 pads
    iotiles = {}
    for ori in Orientation:
        builder = ctx.build_tile(iob, 4,
                                 name="t_io_{}".format(ori.name[0]),
                                 edge=OrientationTuple(False, **{ori.name: True}))
        iotiles[ori] = builder.fill((1., 1.)).auto_connect().commit()

    # ----------------------------------------------------------------
    # 4x4 top-level array:
    #   corners (0,0) (0,3) (3,0) (3,3) → empty
    #   x==0 or x==3 edges              → IO tiles (west / east)
    #   y==0 or y==3 edges              → IO tiles (south / north)
    #   interior (1,1)..(2,2)           → CLB tiles
    # ----------------------------------------------------------------
    builder = ctx.build_array('top', 4, 4, set_as_top=True)
    for x, y in product(range(builder.width), range(builder.height)):
        if x in (0, builder.width - 1) and y in (0, builder.height - 1):
            pass  # corners left empty
        elif x == 0:
            builder.instantiate(iotiles[Orientation.west], (x, y))
        elif x == builder.width - 1:
            builder.instantiate(iotiles[Orientation.east], (x, y))
        elif y == 0:
            builder.instantiate(iotiles[Orientation.south], (x, y))
        elif y == builder.height - 1:
            builder.instantiate(iotiles[Orientation.north], (x, y))
        else:
            builder.instantiate(clbtile, (x, y))

    top = builder.fill(SwitchBoxPattern.cycle_free).auto_connect().commit()

    # Generate VPR arch / RRG and Yosys synthesis scripts
    Flow(
        VPRArchGeneration('vpr/arch.xml'),
        VPR_RRG_Generation('vpr/rrg.xml'),
        YosysScriptsCollection('syn'),
    ).run(ctx)

    ctx.pickle("ctx.tmp.pkl")

# Generate RTL netlist (frame-based programming circuitry)
Flow(
    Materialization('frame'),
    Translation(),
    SwitchPathAnnotation(),
    ProgCircuitryInsertion(),
    VerilogCollection('rtl'),
).run(ctx)

ctx.pickle("ctx.pkl" if len(sys.argv) < 2 else sys.argv[1])
print("\n[build.py] Done. Outputs: vpr/arch.xml, vpr/rrg.xml, syn/, rtl/")
