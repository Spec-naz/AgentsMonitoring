import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys

from . import __version__
from .ledger import Ledger, actor
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
    encrypted = commands.add_parser('backup-age', help='Data-only age encryption; inventory is the default')
    encrypted.add_argument('--root', action='append', default=[])
    encrypted.add_argument('--output', type=Path, required=True)
    encrypted.add_argument('--recipient', required=True, help='Public native age recipient, never a secret identity')
    encrypted.add_argument('--age-executable', type=Path)
    encrypted.add_argument('--execute', action='store_true')
    decrypt = commands.add_parser('rehearse-age', help='Decrypt into private isolated staging; starts no services')
    decrypt.add_argument('--archive', type=Path, required=True)
    decrypt.add_argument('--staging', type=Path, required=True)
    decrypt.add_argument('--identity-file', type=Path, required=True, help='Path to owned private recovery identity; contents are never arguments')
    decrypt.add_argument('--age-executable', type=Path)
    decrypt.add_argument('--sha256', help='Separately recorded expected ciphertext hash')
    capability = commands.add_parser('age-capabilities')
    capability.add_argument('--age-executable', type=Path)
    hook_plan = commands.add_parser('provider-plan', help='Metadata-only hook merge plan; never writes provider config')
    hook_plan.add_argument('--provider', choices=('claude', 'codex'), required=True)
    hook_plan.add_argument('--actor', required=True)
    hook_plan.add_argument('--settings', type=Path)
    hook_plan.add_argument('--state-dir', type=Path)
    hook_plan.add_argument('--include-activity', action='store_true')
    bind = commands.add_parser('provider-bind', help='Declare an exact actor/provider/session binding')
    bind.add_argument('--actor', required=True)
    bind.add_argument('--provider', choices=('claude', 'codex'), required=True)
    bind.add_argument('--session-id', required=True)
    bind.add_argument('--state-dir', type=Path)
    bind.add_argument('--max-age-seconds', type=int, default=900)
    args = parser.parse_args()
    if args.command in ('backup', 'backup-age'):
        from .continuity import export_backup
        roots = {}
        for entry in args.root:
            name, separator, value = entry.partition('=')
            if not separator or not value or name in roots:
                raise ValueError('Use unique --root label=/absolute/path')
            roots[name] = Path(value)
        if args.command == 'backup-age':
            from .encrypted import export_encrypted
            result = export_encrypted(roots, args.output, args.recipient,
                                      ledger_path=args.db if args.db.exists() else None,
                                      age_executable=args.age_executable, dry_run=not args.execute)
        else:
            result = export_backup(roots, args.output, ledger_path=args.db if args.db.exists() else None, dry_run=not args.execute)
    elif args.command == 'rehearse':
        from .continuity import restore_backup
        result = restore_backup(args.archive, args.staging, expected_sha256=args.sha256)
    elif args.command == 'rehearse-age':
        from .encrypted import rehearse_encrypted
        result = rehearse_encrypted(args.archive, args.staging, args.identity_file,
                                    age_executable=args.age_executable, expected_sha256=args.sha256)
    elif args.command == 'age-capabilities':
        from .encrypted import capabilities
        result = capabilities(args.age_executable)
    elif args.command == 'provider-plan':
        from .provider_signals import hook_command, plan_install
        who = actor(args.actor)
        command = '/usr/bin/env AGENTOPS_ACTOR=' + who + ' ' + hook_command(
            args.provider, python=sys.executable, package_root=Path(__file__).resolve().parents[1], state_dir=args.state_dir)
        _, result = plan_install(args.provider, args.settings, command, args.include_activity)
    elif args.command == 'provider-bind':
        from .provider_signals import SESSION_RE
        who = actor(args.actor)
        if not SESSION_RE.fullmatch(args.session_id) or not 60 <= args.max_age_seconds <= 86400:
            raise ValueError('Exact valid session and bounded freshness required')
        config = json.loads(args.config.read_text()) if args.config.exists() else {'schema_version': 1}
        config.setdefault('provider_bindings', {})[who] = {
            'provider': args.provider, 'session_id': args.session_id,
            'state_dir': str(args.state_dir or Path.home() / '.local/state/agentops/provider-signals'),
            'max_age_seconds': args.max_age_seconds}
        atomic_json(args.config, config)
        result = {'bound_actor': who, 'provider': args.provider, 'exact_session': True,
                  'identity_authenticated': False, 'hooks_installed': False}
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
