#!/usr/bin/env bash
# =============================================================================
# run_openfpga.sh
# Build the prga_open_fpga Docker image and run the full PRGA + OpenFPGA
# pipeline for the 2x2 K4/N1 FPGA + 2-bit adder example.
#
# Usage:
#   bash run_openfpga.sh                 # build image if needed, then run
#   bash run_openfpga.sh --build-only    # build the Docker image and exit
#   bash run_openfpga.sh --no-rebuild    # skip docker build, just run
#   bash run_openfpga.sh --clean         # remove generated files, then run
#   bash run_openfpga.sh --interactive   # open bash inside the container
#
# Outputs (under examples/fpga_k4_N1_2x2/):
#   vpr/arch.xml              PRGA VPR architecture (patched for OpenFPGA)
#   openfpga_arch.xml         OpenFPGA circuit-level companion arch
#   syn/result.blif           Synthesised BLIF netlist
#   openfpga_out/rtl/         Fabric Verilog (68 modules, fpga_top.v)
#   openfpga_out/fabric_bitstream.xml   Configuration bitstream
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
IMAGE="prga_open_fpga:latest"
DOCKERFILE="Dockerfile.prga_openfpga"
EXAMPLE="fpga_k4_N1_2x2"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLES_DIR="${REPO_ROOT}/examples"
EXAMPLE_DIR="${EXAMPLES_DIR}/${EXAMPLE}"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
BUILD=true
RUN=true
INTERACTIVE=false
CLEAN=false

for arg in "$@"; do
    case "$arg" in
        --build-only)   RUN=false ;;
        --no-rebuild)   BUILD=false ;;
        --interactive)  INTERACTIVE=true ;;
        --clean)        CLEAN=true ;;
        --help|-h)
            sed -n '/^# Usage/,/^# =====.*Output/p' "$0" | grep -v '^# ====='
            exit 0 ;;
        *)
            echo "[ERROR] Unknown option: $arg" >&2
            exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[run_openfpga] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# Produce a Windows-style forward-slash path (E:/Work/...) that Docker Desktop
# recognises as a drive-letter path when parsing -v host:container.
# MSYS_NO_PATHCONV=1 stops Git Bash from converting the container side path.
host_path() {
    local p="$1"
    # Prefer cygpath (always present in Git Bash / MSYS2) — gives E:/foo form
    if command -v cygpath &>/dev/null; then
        cygpath -m "$p"
        return
    fi
    # Already Windows forward-slash (E:/foo) — pass through
    if [[ "$p" =~ ^[A-Za-z]:/ ]]; then
        printf '%s' "$p"; return
    fi
    # Git-Bash POSIX /e/foo → E:/foo
    if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]]; then
        printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return
    fi
    # WSL /mnt/e/foo → E:/foo
    if [[ "$p" =~ ^/mnt/([a-zA-Z])/(.*) ]]; then
        printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return
    fi
    # Native Linux — pass through unchanged
    printf '%s' "$p"
}

EXAMPLES_MOUNT="$(host_path "${EXAMPLES_DIR}")"
REPO_MOUNT="$(host_path "${REPO_ROOT}")"

# ---------------------------------------------------------------------------
# Step 0: clean generated files
# ---------------------------------------------------------------------------
if [ "$CLEAN" = true ]; then
    info "Cleaning generated files in ${EXAMPLE_DIR}..."
    rm -f  "${EXAMPLE_DIR}/ctx.tmp.pkl"
    rm -f  "${EXAMPLE_DIR}/vpr/arch.xml"
    rm -f  "${EXAMPLE_DIR}/openfpga_arch.xml"
    rm -f  "${EXAMPLE_DIR}/openfpga_flow_generated.openfpga"
    rm -rf "${EXAMPLE_DIR}/openfpga_out"
    info "Clean done."
fi

# ---------------------------------------------------------------------------
# Step 1: Build Docker image
# ---------------------------------------------------------------------------
if [ "$BUILD" = true ]; then
    info "============================================================"
    info "Building Docker image: ${IMAGE}"
    info "Dockerfile: ${DOCKERFILE}"
    info "This may take 20-40 minutes on first run."
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker build \
        -t "${IMAGE}" \
        -f "${REPO_MOUNT}/${DOCKERFILE}" \
        "${REPO_MOUNT}"
    info "Docker image built: ${IMAGE}"
else
    info "Skipping Docker image build (--no-rebuild)."
fi

# ---------------------------------------------------------------------------
# Step 2: Run pipeline (or open interactive shell)
# ---------------------------------------------------------------------------
if [ "$RUN" = false ]; then
    info "Build-only mode — exiting."
    exit 0
fi

info "============================================================"
if [ "$INTERACTIVE" = true ]; then
    info "Opening interactive bash session inside container."
    info "Examples directory mounted at: /opt/examples"
    info "Run the pipeline with:"
    info "  python3.8 /opt/examples/${EXAMPLE}/run_openfpga.py"
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker run -it --rm \
        -v "${EXAMPLES_MOUNT}:/opt/examples" \
        "${IMAGE}" \
        bash
else
    info "Running PRGA + OpenFPGA pipeline for example: ${EXAMPLE}"
    info "Examples directory: ${EXAMPLES_MOUNT} → /opt/examples"
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v "${EXAMPLES_MOUNT}:/opt/examples" \
        "${IMAGE}" \
        python3.8 /opt/examples/${EXAMPLE}/run_openfpga.py

    info "============================================================"
    info "Pipeline complete. Outputs:"
    for f in \
        "vpr/arch.xml" \
        "openfpga_arch.xml" \
        "syn/result.blif" \
        "openfpga_out/result.net" \
        "openfpga_out/fabric_bitstream.xml" \
        "openfpga_out/rtl/fpga_top.v"
    do
        path="${EXAMPLE_DIR}/${f}"
        if [ -f "$path" ]; then
            size=$(du -sh "$path" 2>/dev/null | cut -f1)
            info "  ✓  ${f}  (${size})"
        else
            info "  ✗  ${f}  [MISSING]"
        fi
    done
    info "============================================================"
fi
