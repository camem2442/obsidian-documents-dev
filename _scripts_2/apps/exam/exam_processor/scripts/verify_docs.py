#!/usr/bin/env python3
"""
verify_docs.py — Exam Processor documentation integrity checker.

Checks:
  1. Frontmatter metadata (status: current + architecture: canonical-runtime-v1 → hard fail;
     last_verified_commit → warning only; navigation/plans use their declared status)
  2. Forbidden stale phrases that describe superseded architecture
  3. Code path existence
  4. Required symbols (AST-level, via explicit REQUIRED_SYMBOLS contract table)
  5. Complete document inventory, roles and declared status

Historical docs under docs/reviews/ retain path/status inventory checks but are excluded from stale current-contract checks.
"""

import ast
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent.parent          # .../exam_processor/
DOCS_ROOT = APP_ROOT / "docs"
HISTORICAL_DIRS = {DOCS_ROOT / "reviews"}

# ---------------------------------------------------------------------------
# Role inventory — every docs Markdown has an explicit path and responsibility.
# ---------------------------------------------------------------------------
INVENTORY_PATH = APP_ROOT / "scripts" / "docs_inventory.json"
DOC_INVENTORY = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))["documents"]
ACTIVE_CONTRACT_DOCS = [
    DOCS_ROOT / item["path"] for item in DOC_INVENTORY if item["role"] == "contract"
]

# Legacy historical records (5 docs) — additional banner warning; inventory checks still apply
HISTORICAL_DOCS = [
    DOCS_ROOT / "reviews" / "asset-review-2506-2026-09-10.md",
    DOCS_ROOT / "reviews" / "canonical-contract-review-2026-09-13.md",
    DOCS_ROOT / "reviews" / "hanwangi-inventory-2026-09-13.md",
    DOCS_ROOT / "reviews" / "troubleshooting-2026-09-17-exam-processor-review-ui.md",
    DOCS_ROOT / "reviews" / "uiux-review-2026-09-10.md",
]

# ---------------------------------------------------------------------------
# Forbidden stale phrases  (regex, Korean + mixed)
# ---------------------------------------------------------------------------
FORBIDDEN_PATTERNS = [
    (
        r"정규 레코드를.{0,30}SSOT로 전환하는 일.{0,10}남아",
        "Canonical records are already SSOT (P0~P1-4 완료). Remove 'SSOT 전환 미완료' language.",
    ),
    (
        r"기존 작업 JSON은 계속 처리 런타임 저장소",
        "job.json is now a pure metadata manifest. Runtime state lives in runtime.json.",
    ),
    (
        r"큐 이력은 작업 JSON에 보관",
        "Queue history is stored in runtime.json, not job.json.",
    ),
    (
        r"Store\.save는 현재.{0,20}저장 경계",
        "Store.save() is no longer the production write boundary.",
    ),
    (
        r"정규 JSON을 독립 SSOT로 취급하지 않는다",
        "Canonical JSON IS the independent SSOT. This statement is factually inverted.",
    ),
    (
        r"FormatResult\.reviews",
        "FormatResult.reviews does not exist. Use IngestBatch.records + IngestBatch.review_states.",
    ),
    (
        r"전체 AI 실행 이력의 정규 보존은 별도 과제",
        "AI execution log is stored in runtime.json (ai_log). This is no longer a separate task.",
    ),
    (
        r"KICE\s*(해설은|Master는|마스터는)\s*master\.solution에\s*저장",
        "KICE Master stores pure question content only. Knowledge relations live in links/Q.json.",
    ),
    (
        r"KS\s*파일에\s*question_id.*frontmatter를\s*(주입|추가|삽입)",
        "KS vault is strictly read-only. Do not bulk-inject frontmatter into legacy files.",
    ),
    (
        r"파일명(만으로|을\s*단독으로).*canonical\s*identity",
        "Filename is never identity evidence by itself. Text fingerprint is required.",
    ),
    (
        r"learning_artifact_link_ids.*RelationsDomain",
        "Learning artifacts are knowledge relations stored in library/kice/links/, not RelationsDomain.",
    ),
    (
        r"(knowledge|해설|artifact|산출물)\s*(연결|변경)\s*시\s*content\s*revision\s*(증가|상승|생성)",
        "Knowledge relation mutations must never bump CanonicalQuestionRecord.provenance.revision_id.",
    ),
    (
        r"master\s*레코드(에|의)\s*solution_ids.*SSOT",
        "Master record does not store solution_ids. library/kice/links/ is the multi-solution SSOT.",
    ),
]

# ---------------------------------------------------------------------------
# Required code paths
# ---------------------------------------------------------------------------
REQUIRED_PATHS = [
    APP_ROOT / "domain" / "job_runtime.py",
    APP_ROOT / "domain" / "services" / "record_service.py",
    APP_ROOT / "domain" / "services" / "review_service.py",
    APP_ROOT / "storage" / "store.py",
    APP_ROOT / "ingestion" / "formats" / "base.py",
    APP_ROOT / "pipeline" / "ai.py",
    APP_ROOT / "pipeline" / "work_queue.py",
]

# ---------------------------------------------------------------------------
# Required symbols — explicit contract table (AST-verified)
# ---------------------------------------------------------------------------
REQUIRED_SYMBOLS: dict = {
    "domain/job_runtime.py": ["JobRuntimeState"],
    "domain/services/record_service.py": ["RecordService", "mutate"],
    "domain/services/review_service.py": ["ReviewService", "set_audit_result"],
    "ingestion/formats/base.py": ["IngestBatch", "FormatResult"],
    "pipeline/work_queue.py": ["WorkQueue"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_historical(path: Path) -> bool:
    for hdir in HISTORICAL_DIRS:
        try:
            path.relative_to(hdir)
            return True
        except ValueError:
            pass
    return False


def _read(path: Path):
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _extract_frontmatter(text: str) -> dict:
    """Parse simple key: value YAML frontmatter (no nested structures)."""
    result = {}
    if not text.startswith("---"):
        return result
    end = text.find("\n---", 3)
    if end == -1:
        return result
    block = text[3:end]
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def _ast_symbols(path: Path) -> set:
    """Return all top-level + method names found in a Python file via AST."""
    text = _read(path)
    if text is None:
        return set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_frontmatter_metadata(errors, warnings):
    """Active Contract docs must carry status: current + architecture: canonical-runtime-v1."""
    for doc in ACTIVE_CONTRACT_DOCS:
        text = _read(doc)
        if text is None:
            continue
        fm = _extract_frontmatter(text)
        rel = doc.relative_to(DOCS_ROOT)

        if fm.get("status", "") != "current":
            errors.append(
                f"[FRONTMATTER] {rel}: 'status' must be 'current' (got {fm.get('status','')!r})."
            )
        if fm.get("architecture", "") != "canonical-runtime-v1":
            errors.append(
                f"[FRONTMATTER] {rel}: 'architecture' must be 'canonical-runtime-v1' "
                f"(got {fm.get('architecture','')!r})."
            )
        if "last_verified_commit" not in fm:
            warnings.append(
                f"[FRONTMATTER] {rel}: 'last_verified_commit' is missing (warning only)."
            )


def check_forbidden_patterns(errors):
    """Scan all non-historical docs for stale architectural descriptions."""
    all_docs = list(DOCS_ROOT.rglob("*.md"))
    for doc in sorted(all_docs):
        if _is_historical(doc):
            continue
        text = _read(doc)
        if not text:
            continue
        rel = doc.relative_to(DOCS_ROOT)
        for pattern, message in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                errors.append(f"[STALE] {rel}: {message}\n  (matched pattern: {pattern!r})")


def check_code_paths(errors):
    """Verify required source files exist."""
    for path in REQUIRED_PATHS:
        if not path.exists():
            rel = path.relative_to(APP_ROOT)
            errors.append(f"[PATH] Missing required file: {rel}")


def check_required_symbols(errors):
    """AST-verify that required symbols exist in the declared source files."""
    for rel_str, symbols in REQUIRED_SYMBOLS.items():
        path = APP_ROOT / rel_str
        if not path.exists():
            errors.append(f"[SYMBOL] File not found: {rel_str}")
            continue
        found = _ast_symbols(path)
        for sym in symbols:
            if sym not in found:
                errors.append(
                    f"[SYMBOL] {rel_str}: required symbol '{sym}' not found"
                )


def check_historical_banners(warnings):
    """Historical review docs should carry a Historical snapshot banner."""
    for doc in HISTORICAL_DOCS:
        text = _read(doc)
        if text is None:
            continue
        if "Historical snapshot" not in text and "역사 기록" not in text:
            rel = doc.relative_to(DOCS_ROOT)
            warnings.append(
                f"[BANNER] {rel}: missing Historical snapshot banner."
            )


def check_doc_inventory(errors):
    """Check complete path inventory and declared status; do not infer semantic freshness."""
    paths = [item["path"] for item in DOC_INVENTORY]
    declared = set(paths)
    if len(declared) != len(paths):
        errors.append("[INVENTORY] Duplicate document paths in docs_inventory.json")
    actual = {p.relative_to(DOCS_ROOT).as_posix() for p in DOCS_ROOT.rglob("*.md")}
    for path in sorted(declared - actual):
        errors.append(f"[INVENTORY] Missing declared document: {path}")
    for path in sorted(actual - declared):
        errors.append(f"[INVENTORY] Unregistered document: {path}")
    roles = {"contract", "navigation", "handoff", "status", "registry", "plan", "guide", "format", "reference", "evidence"}
    for item in DOC_INVENTORY:
        path = item["path"]
        if item["role"] not in roles:
            errors.append(f"[INVENTORY] Unknown role: {path}: {item['role']}")
        if path not in actual:
            continue
        fm = _extract_frontmatter((DOCS_ROOT / path).read_text(encoding="utf-8"))
        expected = item.get("status")
        if expected is not None and fm.get("status") != expected:
            errors.append(f"[INVENTORY] Status mismatch: {path}: expected {expected!r}")
        if item["role"] == "contract" and expected != "current":
            errors.append(f"[INVENTORY] Contract must declare current status: {path}")



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    errors = []
    warnings = []

    check_frontmatter_metadata(errors, warnings)
    check_forbidden_patterns(errors)
    check_code_paths(errors)
    check_required_symbols(errors)
    check_historical_banners(warnings)
    check_doc_inventory(errors)

    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  FAIL  {e}")

    if errors:
        print(f"\n[verify_docs] FAIL: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    else:
        print(f"[verify_docs] OK ({len(warnings)} warning(s)).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
