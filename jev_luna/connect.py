"""Local connection defaults; native OAuth remains owned by Codex."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from .local import config_home, service_key, loopback_url


def login_existing(*, allow_login=False):
    from .subscription_auth import request_parameters, CodexLoginRequired
    try:
        request_parameters(30)
        return 'existing_codex_chatgpt'
    except CodexLoginRequired:
        if not allow_login:
            raise ValueError('No Codex ChatGPT login. Run codex login, then luna-decision start; or explicitly use start --login') from None
    # Only an absent login and the explicit --login flag reach this command.
    env = {k: v for k, v in os.environ.items() if k not in (
        'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'CODEX_AUTH_JWT', 'OPENAI_BASE_URL', 'OPENAI_API_BASE')}
    result = subprocess.run(['codex', 'login'], env=env)
    if result.returncode:
        raise ValueError('Native codex login did not complete; current credentials were preserved')
    request_parameters(30)
    return 'codex_chatgpt'


def start(host, port, *, allow_login=False):
    if host not in ('127.0.0.1', 'localhost', '::1') or not 1 <= port <= 65535:
        raise ValueError('Use a loopback host and port 1..65535')
    family = socket.AF_INET6 if host == '::1' else socket.AF_INET
    # Detect collisions before login or creation of app state; never replace a listener.
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            raise ValueError(f'Port {port} is occupied; choose --port with a free port. The existing service was preserved') from None
    from .core import serve
    login = login_existing(allow_login=allow_login)
    print(json.dumps({'login': login, 'new_registration': False,
                      'openai_base_url': base_url(host, port) + '/v1',
                      'jev_base_url': base_url(host, port), 'stop': 'Ctrl-C'}, ensure_ascii=False),
          file=sys.stderr, flush=True)
    serve(host, port)


def base_url(host='127.0.0.1', port=20141):
    return loopback_url(f'http://[{host}]:{port}' if host == '::1' else f'http://{host}:{port}')


def configuration(host, port):
    url = base_url(host, port) + '/v1'
    factory = 'openai_client()' if url == 'http://127.0.0.1:20141/v1' else f'openai_client(base_url={url!r})'
    return {'openai_base_url': base_url(host, port) + '/v1', 'jev_base_url': base_url(host, port),
            'service_key_file': str(config_home() / 'service.key'),
            'python': 'from luna_decision_model import openai_client; client = ' + factory,
            'python_async': 'from luna_decision_model import async_openai_client; client = async_' + factory,
            'start': f'luna-decision start --host {host} --port {port}',
            'doctor': f'luna-decision doctor --url {base_url(host, port)}',
            'timeouts': {'cli_seconds': 120, 'mcp_seconds': 30,
                         'openai_sdk': 'Original SDK defaults or caller options; the service deadline is 120 seconds'},
            'mcp': {'command': str(Path(sys.executable).absolute()),
                    'args': ['-I', '-m', 'luna_decision_model', 'mcp'],
                    'env': {'DECIDE_D_URL': base_url(host, port) + '/v1/systemone',
                            'JEV_LUNA_HOME': str(config_home())}},
            'oauth': 'Native Codex ChatGPT login; service key is local authentication only',
            'startup': {'http_api': 'Explicit start command; never replace an occupied port',
                        'mcp': 'Host starts the stdio adapter; start the HTTP service separately'},
            'recovery': 'Inspect doctor for the exact target build; run native codex login only after confirmed missing login'}


def _sdk_options(base_url, options, *, asynchronous=False):
    loopback_url(base_url, paths=('/v1', '/v1/'))
    if 'api_key' in options:
        raise ValueError('The local client obtains its service key automatically')
    try:
        from openai import DefaultHttpxClient, DefaultAsyncHttpxClient
    except ImportError:
        raise ValueError("Install the SDK extra: python -m pip install 'luna-decision-model[openai]'") from None
    key = service_key()
    if 'http_client' not in options:
        factory = DefaultAsyncHttpxClient if asynchronous else DefaultHttpxClient
        options['http_client'] = factory(trust_env=False)
    return key, options


def openai_client(*, base_url='http://127.0.0.1:20141/v1', **options):
    """The original OpenAI SDK, with the two local connection settings supplied."""
    key, options = _sdk_options(base_url, options)
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=base_url, **options)


def async_openai_client(*, base_url='http://127.0.0.1:20141/v1', **options):
    """Original AsyncOpenAI with the same local endpoint/key contract."""
    key, options = _sdk_options(base_url, options, asynchronous=True)
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=key, base_url=base_url, **options)


def _typesafe_client(base_url, options, *, asynchronous=False):
    loopback_url(base_url)
    if 'api_key' in options:
        raise ValueError('The local client obtains its service key automatically')
    try:
        from typesafe_sdk import TypeSafeClient, AsyncTypeSafeClient
        from typesafe_sdk.constants import DEFAULT_TIMEOUT
    except ImportError:
        raise ValueError('Install typesafe-sdk to use the original TypeSafe SDK') from None
    key = service_key()
    if 'http_client' not in options and 'transport' not in options:
        import httpx2
        http = httpx2.AsyncClient if asynchronous else httpx2.Client
        options['http_client'] = http(timeout=options.get('timeout', DEFAULT_TIMEOUT), trust_env=False)
    factory = AsyncTypeSafeClient if asynchronous else TypeSafeClient
    return factory(api_key=key, base_url=base_url, **options)


def typesafe_client(*, base_url='http://127.0.0.1:20141', **options):
    """Original TypeSafe Client with explicit local root URL and local credential."""
    return _typesafe_client(base_url, options)


def async_typesafe_client(*, base_url='http://127.0.0.1:20141', **options):
    """Original AsyncClient; caller timeout/retry settings are retained."""
    return _typesafe_client(base_url, options, asynchronous=True)
