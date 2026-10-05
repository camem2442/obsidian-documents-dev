#!/usr/bin/env bash

if [ -z "${SCRIPT_DIR:-}" ]; then
    echo "SCRIPT_DIR must be set before sourcing apps/_bootstrap.sh" >&2
    exit 1
fi

STUDY_TOOLS_ROOT="${STUDY_TOOLS_ROOT:-}"
if [ -z "$STUDY_TOOLS_ROOT" ]; then
    candidate="$SCRIPT_DIR"
    while [ "$candidate" != "/" ]; do
        if [ -f "$candidate/program_catalog.json" ] && [ -d "$candidate/apps" ]; then
            STUDY_TOOLS_ROOT="$candidate"
            break
        fi
        candidate="$(dirname "$candidate")"
    done
fi

if [ -z "$STUDY_TOOLS_ROOT" ] || [ ! -f "$STUDY_TOOLS_ROOT/program_catalog.json" ]; then
    echo "_scripts_2 루트를 찾을 수 없습니다: $SCRIPT_DIR" >&2
    exit 1
fi

STUDY_TOOLS_PYTHON="${STUDY_TOOLS_PYTHON:-$STUDY_TOOLS_ROOT/venv/bin/python3}"
if [ ! -x "$STUDY_TOOLS_PYTHON" ]; then
    echo "공용 venv Python을 찾을 수 없습니다: $STUDY_TOOLS_PYTHON" >&2
    exit 1
fi

STUDY_TOOLS_PARENT="$(dirname "$STUDY_TOOLS_ROOT")"
export STUDY_TOOLS_ROOT STUDY_TOOLS_PYTHON
export PYTHONPATH="$STUDY_TOOLS_ROOT:$STUDY_TOOLS_PARENT${PYTHONPATH:+:$PYTHONPATH}"

if ! "$STUDY_TOOLS_PYTHON" -c "import rich" 2>/dev/null; then
    echo "Error: 필수 패키지 'rich'가 공용 venv에 설치되어 있지 않습니다." >&2
    echo "의존성 준비(prepare) 단계에서 설치하십시오: '$STUDY_TOOLS_PYTHON' -m pip install -r '$STUDY_TOOLS_ROOT/requirements.txt'" >&2
    exit 1
fi

# Linux / Cloud VMs: default documents workspace to the git checkout (macOS keeps iCloud in Python).
if [ -z "${DOCUMENTS_ROOT:-}" ] && [ -z "${VAULT_ROOT:-}" ] && [ -z "${OBSIDIAN_VAULT_ROOT:-}" ] && [ -z "${OBSIDIAN_VAULT:-}" ]; then
  if [ "$(uname -s)" != "Darwin" ]; then
    export DOCUMENTS_ROOT="$STUDY_TOOLS_PARENT"
  fi
fi
