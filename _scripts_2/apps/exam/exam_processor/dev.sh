#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../_bootstrap.sh"

PORT="${EXAM_PROCESSOR_PORT:-7893}"

if [ "$PORT" != "7893" ]; then
    echo "Exam Processor 개발 주소는 http://127.0.0.1:7893/으로 고정되어 있습니다." >&2
    echo "EXAM_PROCESSOR_PORT를 제거하고 다시 실행하세요." >&2
    exit 2
fi

echo "Exam Processor development: http://127.0.0.1:${PORT}"
echo "Python changes restart the server; refresh the same browser tab for all changes."

cd "$STUDY_TOOLS_PARENT"
exec "$STUDY_TOOLS_PYTHON" -m uvicorn \
    _scripts_2.apps.exam.exam_processor.server:create_app \
    --factory \
    --host 127.0.0.1 \
    --port "$PORT" \
    --reload \
    --reload-delay 0.5 \
    --reload-dir "$SCRIPT_DIR" \
    "$@"
