#!/usr/bin/env bash
# =============================================================================
# run_buildpy_openfpga.sh
# Build the buildpy_openfpga Docker image and run the pipeline:
#
#   Step 1  build.py               → vpr/arch.xml  (PRGA Python API)
#   Step 1b patch_vpr_arch.py      → vpr/arch.xml  (physical io mode)
#   Step 2  generate_openfpga_arch.py → openfpga_arch.xml
#   Step 3  Yosys (OpenFPGA)       → syn/result.blif
#   Step 4  openfpga shell         → P&R → bitstream → fabric RTL
#
# Usage:
#   bash run_buildpy_openfpga.sh                 # build image + run pipeline
#   bash run_buildpy_openfpga.sh --build-only    # build image and exit
#   bash run_buildpy_openfpga.sh --no-rebuild    # skip build, run pipeline
#   bash run_buildpy_openfpga.sh --interactive   # open bash inside container
#   bash run_buildpy_openfpga.sh --clean         # remove generated files first
# =============================================================================
set -euo pipefail

IMAGE="buildpy_openfpga:latest"
DOCKERFILE="Dockerfile.buildpy_openfpga"
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
        --build-only)  RUN=false ;;
        --no-rebuild)  BUILD=false ;;
        --interactive) INTERACTIVE=true ;;
        --clean)       CLEAN=true ;;
        --help|-h)
            sed -n '/^# Usage/,/^# ====/p' "$0" | grep -v '^# ===='
            exit 0 ;;
        *) echo "[ERROR] Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[buildpy_openfpga] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# Produce E:/foo form that Docker Desktop parses as a drive-letter path.
host_path() {
    local p="$1"
    if command -v cygpath &>/dev/null; then cygpath -m "$p"; return; fi
    if [[ "$p" =~ ^[A-Za-z]:/ ]];           then printf '%s' "$p"; return; fi
    if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]];     then printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return; fi
    if [[ "$p" =~ ^/mnt/([a-zA-Z])/(.*) ]]; then printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"; return; fi
    printf '%s' "$p"
}

EXAMPLES_MOUNT="$(host_path "${EXAMPLES_DIR}")"
REPO_MOUNT="$(host_path "${REPO_ROOT}")"

# ---------------------------------------------------------------------------
# Clean
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
# Build image
# ---------------------------------------------------------------------------
if [ "$BUILD" = true ]; then
    info "============================================================"
    info "Building Docker image : ${IMAGE}"
    info "Dockerfile            : ${DOCKERFILE}"
    info "Difference vs prga_open_fpga:"
    info "  - PRGA Python pkg only (no VTR/Yosys/ABC build) → ~2 GB smaller"
    info "  - arch.xml is the only artefact crossing PRGA → OpenFPGA"
    info "Expected build time   : ~15-20 min (first run)"
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker build \
        -t "${IMAGE}" \
        -f "${REPO_MOUNT}/${DOCKERFILE}" \
        "${REPO_MOUNT}"
    info "Image built: ${IMAGE}"
else
    info "Skipping image build (--no-rebuild)."
fi

[ "$RUN" = false ] && { info "Build-only — exiting."; exit 0; }

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
info "============================================================"
if [ "$INTERACTIVE" = true ]; then
    info "Interactive shell — examples mounted at /opt/examples"
    info "Run pipeline with:"
    info "  python3.8 /opt/examples/${EXAMPLE}/run_buildpy_openfpga.py"
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker run -it --rm \
        -v "${EXAMPLES_MOUNT}:/opt/examples" \
        "${IMAGE}" bash
else
    info "Running pipeline for example: ${EXAMPLE}"
    info "Mount: ${EXAMPLES_MOUNT} → /opt/examples"
    info "============================================================"
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v "${EXAMPLES_MOUNT}:/opt/examples" \
        "${IMAGE}" \
        python3.8 /opt/examples/${EXAMPLE}/run_buildpy_openfpga.py

    info "============================================================"
    info "Pipeline outputs:"
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
