"""Offline CLI: explicit study-note ingestion and canonical-only selection/export."""
import argparse
import json
from pathlib import Path

from ..ingestion.study_revision import StudyRevision
from ..config import DATA_ROOT, DOCUMENTS_ROOT
from ..ingestion.study_service import StudyIngestion
from ..exporters.profiles.markdown import project_to_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=DOCUMENTS_ROOT)
    parser.add_argument('--data-root', type=Path, default=DATA_ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('ingest')
    ingest.add_argument('source', type=Path)
    ingest.add_argument('--adapter', choices=['numbered-qa-v1', 'numbered-table-v1'], required=True)
    ingest.add_argument('--subject', required=True)
    ingest.add_argument('--unit', action='append', default=[])
    ingest.add_argument('--question-type', required=True)
    select = commands.add_parser('select')
    select.add_argument('job', type=Path)
    select.add_argument('--id', action='append', dest='ids')
    select.add_argument('--subject')
    select.add_argument('--source-id')
    select.add_argument('--unit')
    select.add_argument('--question-type')
    select.add_argument('--markdown', action='store_true')
    plan = commands.add_parser('plan')
    plan.add_argument('job', type=Path)
    plan.add_argument('--options-json', type=Path, help='Explicit full adapter/classification options for comparison')
    apply = commands.add_parser('apply')
    apply.add_argument('plan', type=Path)
    apply.add_argument('--confirm-same-item', action='append', default=[], metavar='KEY=REASON')
    recover = commands.add_parser('recover')
    recover.add_argument('job', type=Path)
    args = parser.parse_args()
    service = StudyIngestion(args.repo_root, args.data_root)
    if args.command == 'ingest':
        job = service.ingest(args.source, adapter=args.adapter, subject=args.subject,
                             unit=args.unit, question_type=args.question_type)
        print(json.dumps({'job': str(job), 'records': len(service.select(job))}, ensure_ascii=False))
    elif args.command == 'plan':
        options = json.loads(args.options_json.read_text()) if args.options_json else None
        print(json.dumps(StudyRevision(service).plan(args.job, options=options), ensure_ascii=False, indent=2))
    elif args.command == 'apply':
        confirmations = {}
        for value in args.confirm_same_item:
            key, separator, reason = value.partition('=')
            if not separator or key in confirmations:
                parser.error('Use unique KEY=REASON identity confirmations')
            confirmations[key] = reason
        print(json.dumps(StudyRevision(service).apply(json.loads(args.plan.read_text()),
                         confirmed_same_items=confirmations), ensure_ascii=False))
    elif args.command == 'recover':
        print(StudyRevision(service).recover(args.job))
    else:
        records = service.select(args.job, ids=args.ids, subject=args.subject, source_id=args.source_id,
                                 unit=args.unit, question_type=args.question_type)
        if args.markdown:
            print('\n\n'.join(project_to_markdown(record) for record in records))
        else:
            print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
