#!/usr/bin/env bash
# install.sh — add BenchMark to PATH and install Python dependencies
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARK_BIN="${SCRIPT_DIR}/BenchMark"

echo "BenchMark installer"
echo "==================="

# ── Check Python 3 ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Please install Python 3.8+."
  exit 1
fi
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PYTHON_VER  ($(which python3))"

# ── Install psutil ───────────────────────────────────────────────────────
echo ""
echo "Checking Python dependencies..."
if python3 -c "import psutil" 2>/dev/null; then
  PSUTIL_VER=$(python3 -c "import psutil; print(psutil.__version__)")
  echo "  psutil $PSUTIL_VER — already installed"
else
  echo "  Installing psutil..."
  if python3 -m pip install psutil --user --quiet; then
    PSUTIL_VER=$(python3 -c "import psutil; print(psutil.__version__)")
    echo "  psutil $PSUTIL_VER — installed"
  else
    echo "  WARNING: pip install psutil failed. Try manually: pip3 install psutil --user"
  fi
fi

# ── Make BenchMark executable ─────────────────────────────────────────────
chmod +x "${BENCHMARK_BIN}"
echo ""
echo "Entry point: ${BENCHMARK_BIN}"

# ── Add to PATH ──────────────────────────────────────────────────────────
SHELL_RC=""
case "$SHELL" in
  */zsh)  SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
  *)      SHELL_RC="$HOME/.bashrc" ;;  # fallback
esac

PATH_LINE="export PATH=\"${SCRIPT_DIR}:\$PATH\""
PATH_COMMENT="# BenchMark (Workbench)"

echo ""
if grep -qF "${SCRIPT_DIR}" "${SHELL_RC}" 2>/dev/null; then
  echo "PATH already contains BenchMark directory (in ${SHELL_RC})"
else
  echo "Adding to PATH in ${SHELL_RC}..."
  {
    echo ""
    echo "${PATH_COMMENT}"
    echo "${PATH_LINE}"
  } >> "${SHELL_RC}"
  echo "  Done. Run: source ${SHELL_RC}"
fi

# ── Check R and packages ──────────────────────────────────────────────────
echo ""
echo "Checking R dependencies..."
if command -v Rscript &>/dev/null; then
  R_VER=$(Rscript -e "cat(R.version\$major, R.version\$minor, sep='.')" 2>/dev/null)
  echo "  R $R_VER  ($(which Rscript))"

  echo "  Checking ggplot2..."
  if Rscript -e "library(ggplot2); cat('ok\n')" 2>/dev/null | grep -q "ok"; then
    echo "  ggplot2 — OK"
  else
    echo "  Installing R packages (ggplot2, dplyr, tidyr, scales, gridExtra, RColorBrewer)..."
    Rscript -e "
      pkgs <- c('ggplot2','dplyr','tidyr','scales','gridExtra','RColorBrewer')
      missing <- pkgs[!sapply(pkgs, requireNamespace, quietly=TRUE)]
      if (length(missing) > 0) {
        install.packages(missing, repos='https://cloud.r-project.org', quiet=TRUE)
        cat('Installed:', paste(missing, collapse=', '), '\n')
      } else { cat('All packages present\n') }
    " 2>/dev/null || echo "  WARNING: R package installation failed. Run manually if needed."
  fi
else
  echo "  R not found — BenchMark analyse will not work without R."
  echo "  Install R from: https://www.r-project.org/"
fi

# ── Create state directory ────────────────────────────────────────────────
mkdir -p "$HOME/.benchmark/sessions"
echo ""
echo "State directory: $HOME/.benchmark/"

# ── Done ─────────────────────────────────────────────────────────────────
echo ""
echo "Installation complete!"
echo ""
echo "Quick start:"
echo "  source ${SHELL_RC}"
echo "  screen -S my_tool_run"
echo "  # (in another terminal:)"
echo "  BenchMark go --screen my_tool_run --tool MyTool --dataset my_dataset"
echo "  BenchMark mark \"step_name\"   # label next step"
echo "  BenchMark status             # check progress"
echo "  BenchMark stop               # finish and write CSV"
echo ""
