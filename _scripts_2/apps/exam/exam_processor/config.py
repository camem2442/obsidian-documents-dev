import os
from pathlib import Path

from _scripts_2.vault_paths import app_support_dir, default_documents_root, default_ks_vault

PACKAGE_ROOT = Path(__file__).resolve().parent
DOCUMENTS_ROOT = default_documents_root(PACKAGE_ROOT)
KS_ROOT = default_ks_vault(PACKAGE_ROOT)
DATA_ROOT = Path(os.environ.get("EXAM_PROCESSOR_DATA", str(app_support_dir("Exam Processor"))))
OUTPUT_ROOT = Path(os.environ.get("EXAM_PROCESSOR_OUTPUT", str(DATA_ROOT / "exports")))

SAMPLES = {
    "media": {
        "title": "2606 매체 40~43",
        "track": "매체",
        "path": "1 국어/1 매체/기출/2 기출 유형별 분류/1번 유형 (기획)/2606 1/2606_1.md",
        "solution": "1 국어/1 매체/기출/2 기출 유형별 분류/1번 유형 (기획)/2606 1/해설.md",
    },
    "hwajak1": {"title": "2606 화작 35~37", "track": "화법과 작문", "path": "1 국어/2 화작/2606 1/2606 1.md"},
    "hwajak2": {"title": "2606 화작 38~42", "track": "화법과 작문", "path": "1 국어/2 화작/2606 2/2606 2.md"},
}
SAMPLE_PDF = "1 국어/assets/화작 기출/추출/2606.pdf"
