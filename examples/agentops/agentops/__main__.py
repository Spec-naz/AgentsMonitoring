import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys

from . import __version__
from .ledger import Ledger
from .observe import atomic_json, snapshot


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='AgentOps: explicit tasks, confirmed handoffs, evidence and continuity.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--db', type=Path, default=Path.home() / '.local/state/agentops/tasks.sqlite')
    parser.add_argument('--config', type=Path, default=Path.home() / '.config/agentops/config.json')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('list')
    commands.add_parser('snapshot')
    get = commands.add_parser('show')
    get.add_argument('id')
    hist = commands.add_parser('history')
    hist.add_argument('id')
    create = commands.add_parser('create')
    create.add_argument('--title', required=True)
    create.add_argument('--owner', required=True)
    create.add_argument('--actor', required=True)
    create.add_argument('--request-id')
    for name in ('start', 'progress', 'block', 'offer', 'accept', 'withdraw', 'done', 'cancel'):
        cmd = commands.add_parser(name)
        cmd.add_argument('id')
        cmd.add_argument('--actor', required=True)
        cmd.add_argument('--expected-version', type=int)
        cmd.add_argument('--request-id')
        if name in ('progress', 'block', 'withdraw', 'cancel'):
            cmd.add_argument('--note', required=True)
        if name in ('offer', 'done'):
            cmd.add_argument('--artifact', required=True)
        if name == 'offer':
            cmd.add_argument('--to', required=True)
            cmd.add_argument('--purpose', choices=('work', 'review'), required=True)
    cost = commands.add_parser('cost')
    cost.add_argument('id')
    cost.add_argument('--actor', required=True)
    cost.add_argument('--amount', required=True)
    cost.add_argument('--currency', required=True)
    cost.add_argument('--kind', choices=('actual', 'estimate'), required=True)
    cost.add_argument('--source', required=True)
    cost.add_argument('--request-id')
    backup = commands.add_parser('backup')
    backup.add_argument('--root', action='append', default=[], help='Reviewed root label=/absolute/path; repeatable')
    backup.add_argument('--output', type=Path, required=True)
    backup.add_argument('--execute', action='store_true', help='Without this flag, only inventory the selected roots')
    restore = commands.add_parser('rehearse')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--staging', type=Path, required=True)
    restore.add_argument('--sha256', help='Separately recorded expected archive hash')
    args = parser.parse_args()
    if args.command == 'backup':
        from .continuity import export_backup
        roots = {}
        for entry in args.root:
            name, separator, value = entry.partition('=')
            if not separator or not value or name in roots:
                raise ValueError('Use unique --root label=/absolute/path')
            roots[name] = Path(value)
        result = export_backup(roots, args.output, ledger_path=args.db if args.db.exists() else None, dry_run=not args.execute)
    elif args.command == 'rehearse':
        from .continuity import restore_backup
        result = restore_backup(args.archive, args.staging, expected_sha256=args.sha256)
    else:
        ledger = Ledger(args.db)
        try:
            if args.command == 'init':
                if not args.config.exists():
                    atomic_json(args.config, {'schema_version': 1, 'sessions': {}, 'access_declarations': [],
                                              'stale_after_seconds': 900, 'handoff_after_seconds': 600})
                result = {'database': str(args.db), 'config': str(args.config), 'initialized': True}
            elif args.command == 'list':
                result = ledger.list()
            elif args.command == 'show':
                result = ledger.get(args.id)
            elif args.command == 'history':
                result = ledger.events(args.id)
            elif args.command == 'create':
                result = ledger.create(args.title, args.owner, args.actor, args.request_id)
            elif args.command == 'snapshot':
                config = json.loads(args.config.read_text()) if args.config.exists() else {}
                try:
                    previous = json.loads((args.db.parent / 'snapshot.json').read_text()).get('signals', {})
                except (OSError, ValueError):
                    previous = {}
                result = snapshot(ledger, config, previous=previous)
                atomic_json(args.db.parent / 'snapshot.json', result)
            elif args.command == 'cost':
                amount = Decimal(args.amount)
                if not amount.is_finite() or amount < 0 or amount * 1000000 != (amount * 1000000).to_integral_value():
                    raise ValueError('Cost must have at most 6 decimal places and be nonnegative')
                result = ledger.cost(args.id, args.actor, int(amount * 1000000), args.currency, args.kind, args.source, args.request_id)
            else:
                result = ledger.change(args.id, args.actor, args.command, target=getattr(args, 'to', None),
                                       purpose=getattr(args, 'purpose', None), note=getattr(args, 'note', None),
                                       artifact=getattr(args, 'artifact', None), expected=args.expected_version,
                                       request_id=args.request_id)
        finally:
            ledger.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, InvalidOperation) as error:
        print('AgentOps: ' + str(error), file=sys.stderr)
        sys.exit(1)
