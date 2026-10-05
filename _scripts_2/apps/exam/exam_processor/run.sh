#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../_bootstrap.sh"
exec "$STUDY_TOOLS_PYTHON" -m _scripts_2.apps.exam.exam_processor serve "$@"
