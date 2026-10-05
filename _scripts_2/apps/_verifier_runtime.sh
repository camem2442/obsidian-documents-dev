#!/usr/bin/env bash
# _verifier_runtime.sh - Shared execution contract for component verifiers.
# Sourced by apps and verifiers to enforce canonical Python, validate modes, and standardize outcomes.
set -euo pipefail

# 1. Verify bootstrap was sourced
if [[ -z "${STUDY_TOOLS_ROOT:-}" ]] || [[ -z "${STUDY_TOOLS_PYTHON:-}" ]]; then
    echo "Error: _bootstrap.sh must be sourced before _verifier_runtime.sh" >&2
    exit 1
fi

# 3. Standard Exit Code Specifications
# 0: PASS (All required checks passed)
# 1: FAIL (One or more required checks failed)
# 2: BLOCKED_ENVIRONMENT (Missing required toolchain/capability)
# 3: NOT_APPLICABLE (Component does not apply to current platform)
# 4: INVALID_ARGUMENT (Unknown mode or malformed arguments)
EXIT_PASS=0
EXIT_FAIL=1
EXIT_BLOCKED_ENVIRONMENT=2
EXIT_NOT_APPLICABLE=3
EXIT_INVALID_ARGUMENT=4

# 4. Standard Mode Validation
# Usage: validate_verifier_mode "<mode_input>" <allowed_mode_1> [allowed_mode_2...]
# Example: validate_verifier_mode "${1:-standard}" quick standard full
validate_verifier_mode() {
    local raw_mode="${1:-standard}"
    shift
    if [[ $# -eq 0 ]]; then
        echo "Error: validate_verifier_mode requires explicit allowed modes list." >&2
        exit "$EXIT_FAIL"
    fi
    local allowed=("$@")

    for valid in "${allowed[@]}"; do
        if [[ "$raw_mode" == "$valid" ]]; then
            echo "$raw_mode"
            return 0
        fi
    done

    echo "Error: Invalid or unsupported verification mode '$raw_mode'." >&2
    echo "Supported modes for this component: ${allowed[*]}" >&2
    exit "$EXIT_INVALID_ARGUMENT"
}

# 5. Capability Requirements Checker
# Usage: require_capability "<capability_name>" "<command_to_check>" "<diagnostic_help>"
require_capability() {
    local cap="$1"
    local cmd="$2"
    local help="$3"

    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "BLOCKED_ENVIRONMENT: Required capability '$cap' ('$cmd') is missing." >&2
        echo "Action required: $help" >&2
        exit "$EXIT_BLOCKED_ENVIRONMENT"
    fi
}

# 6. Optional Capability Checker with explicit notice
# Usage: check_capability "<capability_name>" "<command_to_check>" "<reason_if_skipped>"
# Returns 0 if present, 1 if missing (and prints notice)
check_capability() {
    local cap="$1"
    local cmd="$2"
    local reason="$3"

    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    else
        echo "==> [NOTICE] Optional capability '$cap' ('$cmd') not found. Skipping: $reason"
        return 1
    fi
}
