#!/usr/bin/env bash
# =============================================================================
# view_results.sh
# Launch an interactive Docker viewer for PRGA + OpenFPGA build outputs.
#
# Usage:
#   bash view_results.sh                   # full analysis, all sections
#   bash view_results.sh --section logic   # one section only
#   bash view_results.sh --tmux            # tmux: 4-panel simultaneous view
#   bash view_results.sh --shell           # raw interactive bash (no analysis)
#   bash view_results.sh --build           # run the pipeline first, then view
#
# Sections:
#   placement  routing  logic  timing  bitstream  rtl  schematic
#
# Requirements (host):
#   Docker Desktop running, prga_open_fpga:latest image built.
#   For --tmux: tmux is installed inside the container (auto-installed if not).
#   For schematic SVG: graphviz inside the container (auto-installed if not).
# =============================================================================
set -euo pipefail

IMAGE="prga_open_fpga:latest"
EXAMPLE="fpga_k4_N1_2x2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLES_DIR="${REPO_ROOT}/examples"
EXAMPLE_DIR="${EXAMPLES_DIR}/${EXAMPLE}"
ANALYZER="/opt/examples/${EXAMPLE}/analyze_results.py"

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
MODE="all"          # all | section | tmux | shell
SECTION="all"
RUN_PIPELINE=false

for arg in "$@"; do
    case "$arg" in
        --tmux)       MODE="tmux" ;;
        --shell)      MODE="shell" ;;
        --build)      RUN_PIPELINE=true ;;
        --section)    ;;  # handled below
        --section=*)  MODE="section"; SECTION="${arg#--section=}" ;;
        placement|routing|logic|timing|bitstream|rtl|schematic)
                      MODE="section"; SECTION="$arg" ;;
        --help|-h)
            sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            # treat bare word after --section as the section name
            if [[ "${prev_arg:-}" == "--section" ]]; then
                MODE="section"; SECTION="$arg"
            else
                echo "[ERROR] Unknown option: $arg" >&2; exit 1
            fi ;;
    esac
    prev_arg="$arg"
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[view_results] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# Produce E:/foo form that Docker Desktop parses as a drive-letter path.
host_path() {
    local p="$1"
    if command -v cygpath &>/dev/null; then cygpath -m "$p"; return; fi
    if [[ "$p" =~ ^[A-Za-z]:/ ]];            then printf '%s' "$p"; return; fi
    if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]];      then printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return; fi
    if [[ "$p" =~ ^/mnt/([a-zA-Z])/(.*) ]];  then printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return; fi
    printf '%s' "$p"
}

EXAMPLES_MOUNT="$(host_path "${EXAMPLES_DIR}")"

# ---------------------------------------------------------------------------
# Step 0: Optionally run the pipeline first
# ---------------------------------------------------------------------------
if [ "$RUN_PIPELINE" = true ]; then
    info "Running pipeline before viewer ..."
    bash "${REPO_ROOT}/run_openfpga.sh" --no-rebuild
fi

# ---------------------------------------------------------------------------
# Check that output files exist
# ---------------------------------------------------------------------------
if [ ! -f "${EXAMPLE_DIR}/openfpga_out/fabric_bitstream.xml" ]; then
    info "================================================================"
    info "No build outputs found under examples/${EXAMPLE}/openfpga_out/"
    info "Run the pipeline first:"
    info "   bash run_openfpga.sh"
    info "Or pass --build to this script to run it automatically:"
    info "   bash view_results.sh --build"
    info "================================================================"
    exit 1
fi

# ---------------------------------------------------------------------------
# Build the in-container command
# ---------------------------------------------------------------------------

# Bootstrap script run at container start: installs optional deps, then views
bootstrap_all() {
cat <<'BOOT'
#!/usr/bin/env bash
set -e
PY="python3.8"
ANALYZER="/opt/examples/fpga_k4_N1_2x2/analyze_results.py"

# Install graphviz for schematic SVG (fast, already cached in apt lists)
command -v dot >/dev/null 2>&1 || apt-get install -y -q graphviz >/dev/null 2>&1 || true

$PY $ANALYZER all 2>&1 | cat
exec bash
BOOT
}

bootstrap_section() {
cat <<BOOT
#!/usr/bin/env bash
PY="python3.8"
ANALYZER="/opt/examples/fpga_k4_N1_2x2/analyze_results.py"
command -v dot >/dev/null 2>&1 || apt-get install -y -q graphviz >/dev/null 2>&1 || true
\$PY \$ANALYZER ${SECTION} 2>&1 | cat
exec bash
BOOT
}

bootstrap_tmux() {
cat <<'BOOT'
#!/usr/bin/env bash
PY="python3.8"
ANALYZER="/opt/examples/fpga_k4_N1_2x2/analyze_results.py"

# Install deps
command -v tmux >/dev/null 2>&1 || apt-get install -y -q tmux >/dev/null 2>&1 || true
command -v dot  >/dev/null 2>&1 || apt-get install -y -q graphviz >/dev/null 2>&1 || true

SESSION="fpga"
tmux new-session -d -s $SESSION -x 220 -y 55

# ┌──────────────────┬──────────────────┐
# │  Placement+Logic │  Routing         │
# ├──────────────────┼──────────────────┤
# │  Bitstream+RTL   │  Timing          │
# └──────────────────┴──────────────────┘

# Pane 0 (top-left): Placement + Logic
tmux send-keys -t $SESSION \
    "$PY $ANALYZER placement 2>&1 | cat; echo; $PY $ANALYZER logic 2>&1 | cat; exec bash" Enter

# Pane 1 (top-right): Routing
tmux split-window -h -t $SESSION
tmux send-keys -t $SESSION \
    "$PY $ANALYZER routing 2>&1 | cat; exec bash" Enter

# Pane 2 (bottom-left): Bitstream + RTL
tmux select-pane -t 0
tmux split-window -v -t $SESSION
tmux send-keys -t $SESSION \
    "$PY $ANALYZER bitstream 2>&1 | cat; echo; $PY $ANALYZER rtl 2>&1 | cat; exec bash" Enter

# Pane 3 (bottom-right): Timing analysis
tmux select-pane -t 1
tmux split-window -v -t $SESSION
tmux send-keys -t $SESSION \
    "$PY $ANALYZER timing 2>&1 | cat; exec bash" Enter

# Return focus to top-left
tmux select-pane -t 0
tmux attach-session -t $SESSION
BOOT
}

# ---------------------------------------------------------------------------
# Launch container
# ---------------------------------------------------------------------------
info "============================================================"
info "  Image    : ${IMAGE}"
info "  Mount    : ${EXAMPLES_MOUNT} → /opt/examples"
info "  Mode     : ${MODE}${SECTION:+ (${SECTION})}"
info "============================================================"

case "$MODE" in
    shell)
        MSYS_NO_PATHCONV=1 docker run -it --rm \
            -v "${EXAMPLES_MOUNT}:/opt/examples" \
            "${IMAGE}" bash
        ;;
    all)
        MSYS_NO_PATHCONV=1 docker run --rm \
            -v "${EXAMPLES_MOUNT}:/opt/examples" \
            "${IMAGE}" \
            bash -c "$(bootstrap_all)"
        ;;
    section)
        MSYS_NO_PATHCONV=1 docker run --rm \
            -v "${EXAMPLES_MOUNT}:/opt/examples" \
            "${IMAGE}" \
            bash -c "$(bootstrap_section)"
        ;;
    tmux)
        info "Starting tmux with 4-panel view:"
        info "  Top-left  : Placement + Logic"
        info "  Top-right : Routing"
        info "  Bot-left  : Bitstream + RTL"
        info "  Bot-right : Timing (VPR analysis)"
        info "  Ctrl-b + arrow  to switch panes"
        info "  Ctrl-b + z      to zoom a pane"
        info "  Ctrl-b + d      to detach"
        info "============================================================"
        MSYS_NO_PATHCONV=1 docker run -it --rm \
            -v "${EXAMPLES_MOUNT}:/opt/examples" \
            "${IMAGE}" \
            bash -c "$(bootstrap_tmux)"
        ;;
esac
