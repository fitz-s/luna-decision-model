"""Reuse your Codex ChatGPT OAuth login, then serve the local Jev-compatible Decision API."""
import argparse
import asyncio
import json
import os
import subprocess
import sys


def main():
    from . import __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='Luna Decision Model '+__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    server = commands.add_parser('serve', help='Run the local HTTP Decision API')
    server.add_argument('--host', default='127.0.0.1')
    server.add_argument('--port', type=int, default=20141)
    start = commands.add_parser('start', help='Check native Codex OAuth and start the local API in one command')
    start.add_argument('--host', choices=['127.0.0.1', 'localhost', '::1'], default='127.0.0.1')
    start.add_argument('--port', type=int, default=20141)
    start.add_argument('--login', action='store_true', help='Run native codex login only when no login exists')
    config = commands.add_parser('config', help='Print connection settings and MCP configuration without credentials')
    config.add_argument('--host', choices=['127.0.0.1', 'localhost', '::1'], default='127.0.0.1')
    config.add_argument('--port', type=int, default=20141)
    host_config = config.add_mutually_exclusive_group()
    host_config.add_argument('--write-mcp', metavar='PROJECT', help='Explicitly configure only this project .mcp.json')
    host_config.add_argument('--restore-mcp', metavar='PROJECT', help='Remove the unchanged MCP entry installed for this project')
    config.add_argument('--integration', choices=['typesafe', 'openai', 'native-jev', 'codex-selector'], help='Print a connection for an existing application; no model calls')
    config.add_argument('--project', help='Existing absolute project directory for connection guidance (dry-run by default)')
    config.add_argument('--jev-bin', help='Absolute prepared native Jev binary')
    config.add_argument('--selector-bundle', help='Absolute original pinned selector bundle')
    config.add_argument('--node', help='Absolute Node interpreter for the selector')
    config.add_argument('--preparation', help='Successful public preparer receipt binding native adapters to installed assets')
    actions = config.add_mutually_exclusive_group()
    actions.add_argument('--write', action='store_true', help='Write only the project connection rule and its restore journal')
    actions.add_argument('--check', action='store_true', help='Verify the project connection and local credential; no inference')
    actions.add_argument('--restore', action='store_true', help='Remove the unchanged connection guidance installed in this project')
    commands.add_parser('login', help='Reuse an existing Codex ChatGPT login, or run codex login')
    doctor = commands.add_parser('doctor', help='Check the existing login and optional actual ordinary-Luna capabilities')
    doctor.add_argument('--live', action='store_true', help='Run bounded ordinary-Luna smokes using the existing subscription login')
    doctor.add_argument('--output', help='Save a credential-free receipt JSON')
    doctor.add_argument('--url', default=os.environ.get('JEV_LUNA_BASE_URL', 'http://127.0.0.1:20141'),
                        help='Check the running local service health and build at this loopback root URL')
    request = commands.add_parser('decide', help='Read a Jev JSON request from stdin; return typed JSON')
    request.add_argument('--protocol', choices=['jev', 'openai'], default='jev')
    request.add_argument('--url', default=os.environ.get('JEV_LUNA_BASE_URL', 'http://127.0.0.1:20141'))
    request.add_argument('--timeout', type=float, default=120)
    commands.add_parser('mcp', help='Run a stdio MCP adapter (install the mcp extra)')
    args = parser.parse_args()
    try:
        if args.command == 'start':
            from .connect import start
            start(args.host, args.port, allow_login=args.login)
        elif args.command == 'config':
            from .connect import configuration
            if not 1 <= args.port <= 65535: raise ValueError('Use a port 1..65535')
            if args.integration:
                if args.write_mcp or args.restore_mcp: raise ValueError('Choose MCP configuration or integration guidance separately')
                from .integration_config import configuration as integration, project_configuration
                result = integration(args.integration, args.host, args.port, jev_bin=args.jev_bin,
                                     selector_bundle=args.selector_bundle, node=args.node, preparation=args.preparation)
                if args.write or args.check or args.restore:
                    if not args.project: raise ValueError('Integration write/check/restore requires --project')
                if args.project:
                    result = project_configuration(args.project, result, action='write' if args.write else 'check' if args.check else 'restore' if args.restore else 'dry-run')
            elif args.project or args.write or args.check or args.restore or args.jev_bin or args.selector_bundle or args.node or args.preparation:
                raise ValueError('These options require --integration')
            elif args.write_mcp or args.restore_mcp:
                from .host_config import configure_mcp
                result = configure_mcp(args.write_mcp or args.restore_mcp, args.host, args.port,
                                       restore=bool(args.restore_mcp))
            else:
                result = configuration(args.host, args.port)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'serve':
            from .core import serve
            serve(args.host, args.port)
        elif args.command == 'login':
            from .subscription_auth import connection, CodexLoginRequired
            try:
                conn, _, _ = connection(5)
                conn.close()
                print(json.dumps({'login': 'existing_codex_chatgpt', 'new_registration': False}))
            except CodexLoginRequired:
                env = {k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','CODEX_AUTH_JWT','OPENAI_BASE_URL','OPENAI_API_BASE')}
                result = subprocess.run(['codex', 'login'], env=env)
                if result.returncode: return result.returncode
                conn, _, _ = connection(5)
                conn.close()
                print(json.dumps({'login': 'codex_chatgpt', 'new_registration': False}))
        elif args.command == 'mcp':
            try:
                from .mcp import mcp
            except ImportError as error:
                if error.name == 'mcp':
                    print("Install the MCP extra: python -m pip install 'luna-decision-model[mcp]'", file=sys.stderr)
                    return 1
                raise
            mcp.run()
        elif args.command == 'doctor':
            from .doctor import check
            result = check(live=args.live, url=args.url)
            raw = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
            if args.output:
                from .receipts import AtomicReceipt
                AtomicReceipt(args.output, result)
            print(raw, end='')
            return 0 if result['ok'] else 1
        else:
            from .client import request, report_diagnostics
            from .validation import loads
            raw = sys.stdin.buffer.read(64*1024*1024+1)
            if len(raw)>64*1024*1024: raise ValueError('Request body exceeded its operational byte budget')
            body = loads(raw)
            if not isinstance(body, dict): raise ValueError('Request must be a JSON object')
            body.setdefault('model', 'jev-latest' if args.protocol == 'jev' else 'gpt-6-luna')
            result = asyncio.run(request(body, protocol=args.protocol, url=args.url, timeout=args.timeout))
            report_diagnostics(result)
            print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        from .errors import Reject
        if isinstance(error, Reject): message = f'{error.code}: {error.message}'
        elif isinstance(error, (ValueError, OSError)): message = str(error)
        else: message = type(error).__name__+': command failed; credentials were not printed'
        print(message, file=sys.stderr)
        if hasattr(error, 'accounting'):
            print(json.dumps({'decision_error': getattr(error, 'error', {}), 'accounting': error.accounting,
                              'headers': getattr(error, 'headers', {})}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
