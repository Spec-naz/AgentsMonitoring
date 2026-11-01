"""``python -m agentsmon <command>``

  setup       interactive wizard (auto-detect agents, write config, install service)
  status      print live agent + daemon status
  keepalive   one keepalive pass (restart anything dead); --loop to run forever
  dashboard   serve the live status web page
  service     print/install the OS service for boot persistence
  doctor      sanity-check config + tools
  uninstall   stop everything and remove config/state/service
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agentsmon", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    from . import __version__
    p.add_argument("-V", "--version", action="version", version=f"agentsmon {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="interactive setup wizard")
    sub.add_parser("new", help="create a new agent (pick type + name), launch it, and monitor it")
    add = sub.add_parser("add", help="detect and add newly-running agents/daemons (no full re-setup)")
    add.add_argument('--non-interactive', action='store_true', help='register one Agent2Telegram bridge without prompts')
    add.add_argument('--bridge-config')
    add.add_argument('--bridge-python', help='absolute path to the bridge Python interpreter')
    add.add_argument('--bridge-pythonpath', default='')
    add.add_argument('--bridge-state', default='')
    sub.add_parser("update", help="pull the latest code and reload (no re-setup)")
    rs = sub.add_parser("restart", help="restart supervised agents (resuming their conversation)")
    rs.add_argument("names", nargs="*", help="agent names; omit with --all")
    rs.add_argument("--all", action="store_true", help="every enabled agent that is running")
    usage = sub.add_parser('antigravity-usage', help='install or collect official Antigravity quota metadata')
    usage.add_argument('operation', choices=['install', 'collect'])
    usage.add_argument('--profile', required=True)
    usage.add_argument('--pool', required=True)
    usage.add_argument('--snapshot-dir')
    usage.add_argument('--profile-home')
    usage.add_argument('--apply', action='store_true')
    sub.add_parser("status", help="print live status")
    ka = sub.add_parser("keepalive", help="restart anything that died")
    ka.add_argument("--loop", action="store_true", help="run continuously")
    db = sub.add_parser("dashboard", help="serve the status web page")
    db.add_argument("--host"); db.add_argument("--port", type=int)
    sub.add_parser("service", help="print/install the boot service")
    sub.add_parser("doctor", help="diagnose config + tools")
    un = sub.add_parser("uninstall", help="stop and remove everything")
    un.add_argument("--yes", action="store_true")
    args = p.parse_args(argv)

    if args.command == 'antigravity-usage':
        if args.operation == 'install' and not args.profile_home:
            p.error('antigravity-usage install requires --profile-home')
        from . import antigravity_usage
        options = [args.operation, '--profile', args.profile, '--pool', args.pool]
        if args.snapshot_dir: options.extend(['--snapshot-dir', args.snapshot_dir])
        if args.operation == 'install':
            options.extend(['--profile-home', args.profile_home])
            if args.apply: options.append('--apply')
        elif args.apply or args.profile_home:
            p.error('--apply and --profile-home are installation options')
        return antigravity_usage.main(options)
    if args.command == "status":
        from . import status
        print(status.render())
        return 0
    if args.command == "keepalive":
        from . import keepalive
        return keepalive.run(loop=args.loop)
    if args.command == "dashboard":
        from . import dashboard, config
        cfg = config.load()
        dashboard.serve(args.host or cfg["dashboard"]["host"],
                        args.port or cfg["dashboard"]["port"])
        return 0
    if args.command == "setup":
        from . import wizard
        return wizard.run()
    if args.command == "new":
        from . import wizard
        return wizard.new()
    if args.command == "add":
        if args.non_interactive:
            if not args.bridge_config or not args.bridge_python:
                p.error('--non-interactive requires --bridge-config and --bridge-python')
            from . import bridges
            return bridges.add_noninteractive(args)
        from . import wizard
        return wizard.add()
    if args.command == "update":
        from . import updater
        return updater.run()
    if args.command == "restart":
        if not args.names and not args.all:
            p.error("name an agent or use --all")
        from . import restarter
        return restarter.run(args.names, every=args.all)
    if args.command == "service":
        from . import service
        return service.main()
    if args.command == "doctor":
        from . import doctor
        return doctor.run()
    if args.command == "uninstall":
        from . import uninstaller
        return uninstaller.run(yes=args.yes)
    p.error("unknown command")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
