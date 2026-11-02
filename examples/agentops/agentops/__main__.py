import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
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
    regress = commands.add_parser('regress', help='Offline synthetic case checks; never executes agents or deploys')
    operations = regress.add_subparsers(dest='operation', required=True)
    template = operations.add_parser('template', help='Create empty, suite-bound observations')
    template.add_argument('--suite', type=Path, required=True)
    template.add_argument('--output', type=Path, required=True)
    evaluate = operations.add_parser('run', help='Evaluate explicit observations; returns 2 unless all cases pass')
    evaluate.add_argument('--suite', type=Path, required=True)
    evaluate.add_argument('--observations', type=Path, required=True)
    evaluate.add_argument('--revision', required=True)
    evaluate.add_argument('--output', type=Path, required=True)
    comparison = operations.add_parser('compare', help='Compare same-contract reports; never authorizes deployment')
    comparison.add_argument('--baseline', type=Path, required=True)
    comparison.add_argument('--candidate', type=Path, required=True)
    comparison.add_argument('--output', type=Path, required=True)
    metadata = commands.add_parser('metadata-export', help='Allowlisted metadata only; no titles, notes, prompts or error text')
    metadata.add_argument('--output', type=Path, required=True)
    metadata.add_argument('--signals-dir', type=Path)
    metadata.add_argument('--include-references', action='store_true', help='Add local paths of results and reports')
    retention = commands.add_parser('retention', help='Plan by default; deletes nothing without an explicit apply')
    stages = retention.add_subparsers(dest='stage', required=True)
    planned = stages.add_parser('plan')
    planned.add_argument('--signals-dir', type=Path)
    applied = stages.add_parser('apply', help='Only an explicit non-production folder, only a reviewed plan')
    applied.add_argument('--signals-dir', type=Path, required=True)
    applied.add_argument('--plan-sha256', required=True)
    applied.add_argument('--output', type=Path, required=True)
    timeline = commands.add_parser('timeline', help='Read-only history of one task: who, when, on what evidence, and problems on record')
    timeline.add_argument('id')
    run_event = commands.add_parser('run-event', help='Record one step of a concrete run; no prompts, responses or error text')
    run_event.add_argument('id')
    run_event.add_argument('--run-id', help='Omit to start a new run')
    run_event.add_argument('--parent-id')
    run_event.add_argument('--actor', required=True)
    run_event.add_argument('--action', required=True)
    run_event.add_argument('--source', required=True, choices=('agentops', 'provider-hook', 'operator'))
    run_event.add_argument('--evidence', required=True, choices=('declared', 'observed'))
    run_event.add_argument('--outcome', default='unknown', choices=('ok', 'failed', 'unknown'))
    run_event.add_argument('--error-code')
    run_event.add_argument('--usage-tokens', type=int)
    run_event.add_argument('--request-id')
    package = commands.add_parser('package', help='Versioned work packages; builds and checks files, runs nothing')
    package_steps = package.add_subparsers(dest='step', required=True)
    built = package_steps.add_parser('build', help='Add real hashes and sizes to a draft; never overwrites')
    built.add_argument('--draft', type=Path, required=True)
    built.add_argument('--output', type=Path, required=True)
    built.add_argument('--base', type=Path, help='Folder for relative source paths in the draft')
    checked = package_steps.add_parser('check', help='Is it usable now? Returns 2 when not')
    checked.add_argument('file', type=Path)
    checked.add_argument('--with-task', action='store_true', help='Also compare with the task in the ledger')
    rendered = package_steps.add_parser('render', help='Print the text a worker gets')
    rendered.add_argument('file', type=Path)
    compared = package_steps.add_parser('compare', help='Volume against a broad context; no model is run')
    compared.add_argument('file', type=Path)
    compared.add_argument('--broad', type=Path, nargs='+', required=True)
    compared.add_argument('--facts', type=Path)

    brief = commands.add_parser('brief', help='Prepare the start of one task; reads only, exit 0/10/20/30 says who continues')
    brief.add_argument('id')
    brief.add_argument('--for', dest='for_actor', required=True)
    brief.add_argument('--guard-db', type=Path)
    brief.add_argument('--owner-actor', default='michal')
    queue = commands.add_parser('queue', help='Read-only list of what waits for a decision, repair, review or takeover')
    queue.add_argument('--owner-actor', default='michal')
    queue.add_argument('--for', dest='waiting_on')
    verify = commands.add_parser('verify', help='Read-only result check: exact folder, files and real hashes; returns 2 unless pass')
    verify.add_argument('--spec', type=Path, required=True)
    verify.add_argument('--output', type=Path, required=True)
    verify.add_argument('--task')
    verify.add_argument('--actor')
    verify.add_argument('--expected-version', type=int)
    verify.add_argument('--request-id')
    args = parser.parse_args()
    if args.command in ('metadata-export', 'retention'):
        from . import metadata as policy
        from .regression import write_report
        signals = args.signals_dir or policy.DEFAULT_SIGNAL_DIR
        try:
            if args.command == 'metadata-export':
                result = policy.build_export(args.db, signals, args.include_references)
                write_report(args.output, result)
                print(json.dumps(result['counts'], indent=2))
                return
            try:
                config = json.loads(args.config.read_text()) if args.config.exists() else {}
            except (OSError, ValueError):
                raise ValueError('Configuration cannot be read') from None
            if args.stage == 'plan':
                result = policy.plan(config, signals, args.db)
            else:
                result = policy.apply_plan(config, signals, args.plan_sha256, args.output)
        except OSError:  # paths can hold anything, so these commands never print raw file errors
            raise ValueError('A file for this command could not be read or written') from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command in ('timeline', 'run-event'):
        import time
        import uuid
        try:
            if args.command == 'timeline':
                from . import timeline as history
                try:
                    config = json.loads(args.config.read_text()) if args.config.exists() else {}
                except (OSError, ValueError):
                    config = {}  # the timeline stands without it; a signal then reads as not assigned
                print(json.dumps(history.build(args.db, args.id, config), ensure_ascii=False, indent=2))
                return
            ledger = Ledger(args.db)
            try:
                result = ledger.record_run_event({
                    'task_id': args.id, 'run_id': args.run_id or str(uuid.uuid4()), 'parent_id': args.parent_id, 'at': time.time(), 'source': args.source,
                    'evidence': args.evidence, 'actor': args.actor, 'action': args.action, 'outcome': args.outcome, 'error_code': args.error_code,
                    'usage_tokens': args.usage_tokens}, args.request_id, generated=('at',) if args.run_id else ('at', 'run_id'))
            finally:
                ledger.close()
            print(json.dumps(result, indent=2))
            return
        except (OSError, sqlite3.Error):  # a path can hold anything, so no raw file error reaches the output
            raise ValueError('A file for this command could not be read or written') from None
    if args.command == 'package':
        from . import regression, work_package as wp
        try:
            if args.step == 'build':
                result = wp.build(regression.read_document(args.draft), args.base)
                regression.write_report(args.output, result)
                print(json.dumps(wp.check(result), ensure_ascii=False, indent=2))
                return
            document = wp.load_package(args.file)
            if args.step == 'render':
                sys.stdout.write(wp.render(document))
                return
            if args.step == 'compare':
                facts = regression.read_document(args.facts) if args.facts else None
                print(json.dumps(wp.compare(document, args.broad, facts), ensure_ascii=False, indent=2))
                return
            result = wp.check(document, args.db if args.with_task else None)
        except (OSError, sqlite3.Error):  # a path can hold anything, so no raw file error reaches the output
            raise ValueError('A file for this command could not be read or written') from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result['ready']:
            raise SystemExit(2)
        return

    if args.command == 'brief':
        from . import brief as routine
        result = routine.prepare(args.db, args.id, args.for_actor, args.guard_db or routine.DEFAULT_GUARD, args.owner_actor)
        print(routine.stable_json(result))
        raise SystemExit(routine.EXIT[result['ending']])
    if args.command == 'verify':
        from . import postcheck, regression
        if bool(args.task) != bool(args.actor):
            raise ValueError('--task and --actor belong together')
        spec = regression.read_document(args.spec)
        postcheck.refuse_own_effects(spec, postcheck.effect_paths(str(args.output.expanduser()), args.db.expanduser() if args.task else None))
        result = postcheck.run(spec)
        regression.write_report(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.task:
            report = args.output.expanduser().absolute()
            ledger = Ledger(args.db)
            try:
                ledger.record_postcheck(args.task, args.actor, result['verdict'], str(report),
                                        postcheck.report_digest(report), args.expected_version, args.request_id)
            finally:
                ledger.close()
        if result['verdict'] != 'pass':
            raise SystemExit(2)
        return
    if args.command == 'regress':
        from . import regression
        if args.operation == 'template':
            result = regression.observation_template(regression.read_document(args.suite))
            blocked = False
        elif args.operation == 'run':
            result = regression.run(regression.read_document(args.suite), regression.read_document(args.observations), args.revision)
            blocked = any(row['verdict'] != 'pass' for row in result['results'])
        else:
            result = regression.compare(regression.read_document(args.baseline), regression.read_document(args.candidate))
            blocked = result['quality_gate'] == 'blocked'
        regression.write_report(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if blocked:
            raise SystemExit(2)
        return
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
            elif args.command == 'queue':
                from .review_queue import build
                result = build(ledger, args.owner_actor, waiting_on=args.waiting_on)
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
