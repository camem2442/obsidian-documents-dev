"""Subject rules shared by workbook formats, without changing source layouts."""


MATH_EXTRACT = """
수학 교재이다. 개념 설명, 예제, 각주, CHECK, 원문에 있는 여러 해법을 순서대로 보존한다.
순열·조합의 왼쪽 아래 첨자, 분수의 분자/분모, 지수, 부등호, 구간 끝점을 정확히 전사한다.
인라인은 $...$, 독립 수식은 $$...$$를 쓰고 여러 줄 계산은 aligned 환경으로 보존한다.
객관식과 주관식을 구분한다. 주관식 정답을 객관식 선지로 바꾸지 않는다.
주어진 원본 영역의 내용만 전사한다. 원문에 없는 풀이, 계산 결과, 정답은 생성하지 않는다.
도형·그래프·배치도는 원본 크롭으로 보존한다. diagrams box는 도형·표·색칠 영역만 감싸고 풀이 문장·각주·CHECK 문구는 box 밖 body에 둔다.
보이지 않는 길이·좌표·관계는 추측하지 않는다.
"""

MATH_AUDIT = """
수학 원본 대조이다. 수식 문법과 별도로 부등호·분모·지수·첨자·합의 범위·조건 누락을 확인한다.
객관식 선지와 주관식 정답, 여러 해법·각주·도형·그래프의 원문 충실도를 확인한다.
수식을 계산하거나 다른 풀이로 고쳐서 일치 여부를 판단하지 않는다.
"""


def prompt_suffix(subject, operation, item=None):
    if subject != "수학":
        if operation == "extract" and item and item.get("kind") == "concept":
            from ..pipeline.concept_extract import CONCEPT_EXTRACT
            return CONCEPT_EXTRACT
        return ""
    if operation == "extract":
        if item and item.get("kind") == "concept":
            from ..pipeline.concept_extract import MATH_CONCEPT_EXTRACT
            return MATH_CONCEPT_EXTRACT
        return MATH_EXTRACT
    return MATH_AUDIT
