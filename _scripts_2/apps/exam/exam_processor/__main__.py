import argparse
import json
import socket

from .storage.store import Store


def main():
    parser = argparse.ArgumentParser(description="Exam Processor")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=7893)
    workbook = commands.add_parser("workbook", help="문제집 목차 확인 및 PDF 분할")
    workbook.add_argument("arguments", nargs=argparse.REMAINDER)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--pdf", required=True)
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--track", choices=["매체", "화법과 작문", "확률과 통계", "영어"], required=True)
    ingest.add_argument("--pages", default="")
    ingest.add_argument("--solution", default="")
    ingest.add_argument("--script", default="", help="평가원 영어 듣기 대본 PDF")
    ingest.add_argument("--answer", default="", help="평가원 영어 정답 이미지/PDF (원자료로만 보존)")
    ingest.add_argument("--format", default="")
    ingest.add_argument("--structure", default="")
    ingest.add_argument("--layout", default="")
    migrate = commands.add_parser("migrate", help="v1 작업 JSON을 v2 정규 저장소 구조로 이관")
    migrate.add_argument("--job", help="이관할 작업 ID")
    migrate.add_argument("--all", action="store_true", help="모든 v1 작업 이관")
    migrate.add_argument("--dry-run", action="store_true", help="실제 쓰기 없이 확인만 수행")
    args = parser.parse_args()
    if args.command == "migrate":
        from .storage.migration import migrate_job, migrate_all_jobs
        store = Store()
        if args.job:
            job_dir = store.job_dir(args.job)
            res = migrate_job(job_dir, dry_run=args.dry_run)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif getattr(args, "all", False):
            results = migrate_all_jobs(store.root / "jobs", dry_run=args.dry_run)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print("이관할 작업 ID(--job <id>) 또는 --all 옵션을 지정하세요.")
    elif args.command == "workbook":
        from .scripts.workbook import main as workbook_main
        workbook_main(args.arguments)
    elif args.command == "ingest":
        job = Store().import_file(args.pdf, args.source, args.track, args.pages, args.solution,
                                 format_id=args.format, structure=args.structure, layout=args.layout,
                                 script=args.script, answer=args.answer)
        print(json.dumps({"job_id": job["id"], "items": len(job["items"]), "warnings": job["warnings"]}, ensure_ascii=False))
    else:
        import uvicorn
        from .server import create_app
        # Port 0 is reserved for disposable tests. User-facing runs keep one stable URL.
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError as exc:
            sock.close()
            raise SystemExit(
                f"Exam Processor가 127.0.0.1:{args.port}을 사용할 수 없습니다. "
                "이미 실행 중이면 http://127.0.0.1:7893/을 열고, 다른 프로그램이면 종료한 뒤 다시 실행하세요."
            ) from exc
        sock.listen(128)
        port = sock.getsockname()[1]
        print(f"Exam Processor: http://127.0.0.1:{port}", flush=True)
        server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
        server.run(sockets=[sock])


if __name__ == "__main__":
    main()
