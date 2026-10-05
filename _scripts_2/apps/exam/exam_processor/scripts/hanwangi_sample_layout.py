"""Reviewed physical-page regions for the first Hanwangi 2026 sample only."""
import argparse
import json
from pathlib import Path

from ..storage.store import atomic_json
from ..pipeline.workbook import read_structure, verify_sources
from ..ingestion.formats.math.workbook_layout import validate_layout, match_records


def build(structure, anchor_root):
    records = []
    def region(role, page, box):
        return {"document": role, "page": page, "bbox": box,
                "width": 623.52001953125, "height": 850.3200073242188}
    def add(node, key, kind, number, spans, subtype="", match_key=""):
        records.append({"id": key, "node": node, "kind": kind, "number": number,
                        "subtype": subtype, "match_key": match_key,
                        "regions": [region(*s) for s in spans]})
    def example(key, number, body, solution):
        add("D1", key, "question", number, body, "example", key)
        add("D1", key + "-solution", "solution", number, solution, match_key=key)

    add("A1", "A1-heading", "concept", "A1-heading", [("problem",16,[50,151,303,215])])
    add("A1", "A1-example", "question", "A1", [("problem",16,[50,215,303,330])], "example")
    add("A1", "A1-example-solution", "solution", "A1", [("problem",16,[50,335,303,450])])
    add("A1", "A1-concept", "concept", "A1-concept", [("problem",16,[50,458,303,735])])
    add("D1", "D1-heading", "concept", "D1-heading", [("problem",42,[188,78,571,233])])
    example("D1-1-EX01", "1-EX01", [("problem",42,[188,238,571,350])],
            [("problem",42,[188,353,571,760]),("problem",43,[52,76,575,312])])
    example("D1-1-EX02", "1-EX02", [("problem",43,[52,337,575,421])],
            [("problem",43,[52,428,575,752]),("problem",44,[193,76,573,380])])
    add("D1", "D1-concept-1", "concept", "D1-concept-1", [("problem",44,[193,390,573,640])])
    add("D1", "D1-concept-2", "concept", "D1-concept-2",
        [("problem",45,[52,80,575,113]),("problem",45,[52,300,575,778])])
    example("D1-2-EX01", "2-EX01", [("problem",45,[52,115,575,181])],
            [("problem",45,[52,183,575,295])])
    add("D1", "D1-concept-3", "concept", "D1-concept-3",
        [("problem",46,[193,80,575,100]),("problem",46,[193,325,575,490])])
    example("D1-3-EX01", "3-EX01", [("problem",46,[193,101,575,138])],
            [("problem",46,[193,141,575,311])])
    example("D1-3-EX02", "3-EX02", [("problem",46,[193,501,575,541])],
            [("problem",46,[193,543,575,768]),("problem",47,[56,80,577,195])])
    add("D1", "D1-concept-4", "concept", "D1-concept-4", [("problem",47,[56,210,577,380])])

    endings = {"A1": [170,340,550,210,530,200],
               "D1": [320,580,350,590,320,580,250,550,250,240,555,270,230,280,280,280,300]}
    for node in ("A1", "D1"):
        anchors = json.loads((anchor_root / f"{node}-anchors.json").read_text())["anchors"]
        for a in sorted(anchors["problem"], key=lambda a:a["number"]):
            n = int(a["number"].split("·")[1]); right = a["bbox"][0] > 300
            box = [310 if right else 48, a["bbox"][1]-8, 584 if right else 306, endings[node][n-1]]
            add(node, f"{node}-{n:02}", "question", a["number"], [("problem",a["page"],box)])
        solutions = list(anchors["solution"])
        if node == "A1":
            solutions.append({"number":"A1·05","page":6,"bbox":[308,358,342,368]})
        solutions.sort(key=lambda a:(a["page"],a["bbox"][0]>300,a["bbox"][1]))
        pages = [6,7] if node == "A1" else list(range(40,50))
        columns = [(p,c) for p in pages for c in (0,1)]
        for index, a in enumerate(solutions):
            begin = columns.index((a["page"],int(a["bbox"][0]>300)))
            nxt = solutions[index+1] if index+1<len(solutions) else None
            end = columns.index((nxt["page"],int(nxt["bbox"][0]>300))) if nxt else len(columns)-1
            spans = []
            for ci in range(begin,end+1):
                p,c = columns[ci]
                if (node == "A1" and p==7 and c==1) or (node == "D1" and p==47 and c==1):
                    continue  # Visually checked blank columns, not omitted solutions.
                left,right = ((52,322) if c==0 else (323,592)) if p%2 else ((32,300) if c==0 else (302,578))
                top = a["bbox"][1]-8 if ci==begin else 72
                bottom = nxt["bbox"][1]-8 if nxt and ci==end else 775
                if bottom>top:
                    spans.append(("solution",p,[left,top,right,bottom]))
            add(node,a["number"].replace("·","-")+"-solution","solution",a["number"],spans)
    layout = {"schema_version":1,"workbook_id":structure["id"],"status":"confirmed",
              "evidence":"2026-09-13 assistant visual boundary review of all 26 sample pages; not human content approval",
              "scope":["A1","D1"],"items":records,
              "excluded":"Running headers, page numbers, decorative tabs, blank space and navigation QR; pedagogical CHECK, alternate solutions and concept callouts retained.",
              "corrections":["A1 solution 05 missing from OCR added from PDF6 right column",
                             "Solution reading order: left column then right, including following pages",
                             "D1 EX numbers scoped by subsection 1/2/3"]}
    validate_layout(layout,structure)
    assert all(m["status"]=="matched" for m in match_records(layout).values())
    assert len([r for r in records if r["kind"]=="question"])==29
    return layout


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    args=parser.parse_args()
    structure=read_structure(args.root/"structure.json")
    verify_sources(structure)
    target=args.root/"layout.json"
    if target.exists():
        raise ValueError("Existing layout is preserved; review changes in a new directory.")
    layout=build(structure,args.root)
    atomic_json(target,layout)
    print(json.dumps({"path":str(target),"records":len(layout["items"])}))


if __name__=="__main__":
    main()
