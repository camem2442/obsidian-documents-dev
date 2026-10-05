"""Printed TOC of the inspected 2026 probability workbook, not a scan parser."""


def profile():
    # Printed page starts are evidence from the TOC, never assumed physical PDF pages.
    rows = [
        (0, "A1", "경우의 수", "기본 순열 연습", 16, 6),
        (0, "A2", "경우의 수", "같은 것이 있는 순열 계산", 18, 8),
        (0, "A3", "경우의 수", "기본 조합 연습", 20, 10),
        (0, "A4", "경우의 수", "이항정리 계산", 22, 12),
        (0, "B1", "확률", "확률과 벤다이어그램", 26, 16),
        (0, "B2", "확률", "조건부확률 계산 (1)", 30, 24),
        (0, "B3", "확률", "조건부확률 계산 (2)", 34, 28),
        (0, "C1", "통계", "이항분포 계산", 38, 36),
        (1, "D1", "경우의 수", "Pattern 01", 42, 40),
        (1, "D2", "경우의 수", "Pattern 02", 54, 50),
        (1, "D3", "경우의 수", "Pattern 03", 60, 54),
        (1, "D4", "경우의 수", "Pattern 04", 70, 66),
        (1, "D5", "경우의 수", "Pattern 05", 92, 96),
        (1, "D6", "경우의 수", "Pattern 06", 98, 102),
        (1, "E1", "확률", "Pattern 07", 102, 104),
        (1, "E2", "확률", "Pattern 08", 132, 146),
        (1, "E3", "확률", "Pattern 09", 136, 150),
        (1, "E4", "확률", "Pattern 10", 162, 186),
        (1, "E5", "확률", "Pattern 11", 166, 188),
        (1, "F1", "통계", "Pattern 12", 182, 202),
        (1, "F2", "통계", "Pattern 13", 192, 210),
        (1, "F3", "통계", "Pattern 14", 198, 214),
        (1, "F4", "통계", "Pattern 15", 206, 218),
        (1, "F5", "통계", "Pattern 16", 218, 228),
        (1, "F6", "통계", "Pattern 17", 236, 246),
        (1, "F7", "통계", "Pattern 18", 240, 248),
        (1, "F8", "통계", "Pattern 19", 256, 266),
        (2, "G", "경우의 수", "핵심", 268, 272),
        (2, "H", "확률", "핵심", 272, 276),
        (2, "I", "통계", "핵심", 274, 280),
        (3, "J", "경우의 수", "2005-2025 기출", 280, 286),
        (3, "K", "확률", "2005-2025 기출", 300, 306),
        (3, "L", "통계", "2005-2025 기출", 304, 312),
        (4, "M", "경우의 수", "1994-2004 기출", 318, 326),
        (4, "N", "확률", "1994-2004 기출", 322, 330),
        (4, "O", "통계", "1994-2004 기출", 326, 334),
    ]
    nodes = [{"id": key, "part": part, "chapter": chapter, "title": title,
              "printed_starts": {"problem": p, "solution": s},
              "spans": {r: {"status": "pending", "pages": [], "evidence": ""}
                        for r in ("problem", "solution")}}
             for part, key, chapter, title, p, s in rows]
    sections = [
        ("1-1", "순열과 조합", ("A1", "A2", "A3", "D1", "D2", "D3", "D4")),
        ("1-2", "이항정리", ("A4", "D5", "D6")),
        ("2-1", "확률의 뜻과 활용", ("B1", "E1")),
        ("2-2", "조건부확률", ("B2", "B3", "E2", "E3", "E4", "E5")),
        ("3-1", "확률분포", ("C1", "F1", "F2", "F3", "F4", "F5", "F6")),
        ("3-2", "통계적 추정", ("F7", "F8")),
    ]
    for node in nodes:
        for code, title, ids in sections:
            if node["id"] in ids:
                node.update(section_code=code, section_title=title)
    return {"id": "hanwangi_2026_probability", "version": "1.1.0",
            "source": "2026 한완기 확률과통계", "page_counts": {"problem": 332, "solution": 341},
            "nodes": nodes,
            "samples": {"A1": {"problem": [16, 17], "solution": [6, 7]},
                        "D1": {"problem": list(range(42, 54)), "solution": list(range(40, 50))}}}
