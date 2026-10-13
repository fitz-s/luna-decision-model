"""Check original subscription routing; capability smokes are separate from Jev quality qualification."""
import base64
import hashlib
import http.client
import json
import platform
import socket
import struct
import threading
import time
from pathlib import Path
import zlib
from urllib.parse import urlsplit

from . import __version__
from .errors import Reject
from .lifecycle import RequestContext


def identity():
    root = Path(__file__).parent.parent
    owned = sorted(p for name in ('jev_luna', 'luna_decision_model') for p in (root/name).rglob('*')
                   if p.is_file() and p.suffix in ('.py', '.mjs', '.patch', '.md', '.json', '.go'))
    digest = hashlib.sha256()
    for path in owned:
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def red_png():
    def chunk(kind, content):
        return struct.pack('!I',len(content))+kind+content+struct.pack('!I',zlib.crc32(kind+content)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',8,8,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\x00'+b'\xff\x00\x00'*8)*8))+chunk(b'IEND',b''))


def server_health(url, *, timeout=3):
    from .local import loopback_url
    from .validation import loads
    target = urlsplit(loopback_url(url))
    conn = http.client.HTTPConnection(target.hostname, target.port, timeout=timeout)
    started = time.monotonic()
    watchdog = None
    response = None
    try:
        conn.connect()
        channel = conn.sock
        def abort():
            try: channel.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        watchdog = threading.Timer(max(.001, timeout-(time.monotonic()-started)), abort)
        watchdog.daemon = True
        watchdog.start()
        conn.request('GET', '/health')
        response = conn.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise ValueError('Invalid target health response')
        health = loads(raw)
        if not isinstance(health, dict) or health.get('ok') is not True:
            raise ValueError('Invalid target health response')
        return {'reachable': True, 'liveness': True, 'native_inference_verified': False,
                **{key: health.get(key) for key in ('build_sha256', 'readout_profile', 'transport', 'model',
                    'reasoning_policy', 'deadline_seconds', 'local_auth_required')}}
    finally:
        if watchdog is not None: watchdog.cancel()
        if response is not None: response.close()
        conn.close()


def check(*, live=False, url=None):
    from . import core
    result = {'version': __version__, 'build_sha256': identity(), 'python': platform.python_version(),
              'platform': platform.system(), 'transport': core.TRANSPORT, 'model': 'gpt-6-luna',
              'live_checked': live, 'quality_qualified': False, 'native_speed_parity': False,
              'new_registration': False, 'ok': False}
    result['client_build_sha256'] = result['build_sha256']
    target_ready = True
    if url is not None:
        from .local import loopback_url
        url = loopback_url(url)
        result['target_url'] = url
        try:
            target = server_health(url)
            target['build_matches_client'] = target['build_sha256'] == result['client_build_sha256']
            target['profile_matches_client'] = target['readout_profile'] == core.READOUT_PROFILE
            target_ready = (target['build_matches_client'] and target['profile_matches_client']
                            and target['transport'] == 'subscription' and target['model'] == 'gpt-6-luna')
            target['ready'] = target_ready
            target['action'] = 'none' if target_ready else 'Start this installation on a free port and point the caller to that port'
        except (OSError, ValueError, http.client.HTTPException):
            target_ready = False
            target = {'reachable': False, 'liveness': False, 'ready': False,
                      'native_inference_verified': False, 'action': 'Run luna-decision start on the requested free port'}
        result['target_server'] = target
    try:
        if core.TRANSPORT == 'subscription':
            from .subscription_auth import connection
            conn, _, _ = connection(5)
            conn.close()
            result['login'] = 'existing_codex_chatgpt'
            result['auth_profile'] = {'owner':'native_codex_rpc','account_binding':'account/read.workspaceRouting',
                'jwt_account_inference':False,'backend_origin':'https://chatgpt.com','routing_policy':'native_same_origin_NO_CONSTRAINT_or_us_or_us_cr',
                'agent_turns':0,'persistent_credential_copy':False}
        result['login_ready'] = True
        if not live:
            result['ok'], result['capability_gate'] = target_ready, 'not_run'
            return result
        deadline = time.monotonic()+60
        result['live_inference_scope'] = 'client_native_transport'
        receipts = []
        result['receipts'] = receipts  # Retain finished smokes when a later call fails.
        for evidence in ('The parcel is red and damaged.', 'Filler context. '*900+'\nThe parcel is red and damaged.'):
            state, qs = core.parse_systemone({'model':'gpt-6-luna','state':evidence,'questions':{
                'damaged':{'type':'noul','instructions':'Is the parcel damaged?'},
                'color':{'type':'choice','instructions':'What color is the parcel?','criteria':{'red':'red','blue':'blue'}},
                'damage':{'type':'score','instructions':'Use damaged when the parcel is damaged.','criteria':['intact','damaged']}}})
            started=time.monotonic()
            result['active_smoke']={'kind':'text','reasoning_effort':core.build_upstream(state,qs)['reasoning']['effort']}
            answers,usage,_=core.decide(state,qs,context=RequestContext(deadline))
            receipt={'kind':'text','reasoning_effort':core.build_upstream(state,qs)['reasoning']['effort'],
                     'seconds':time.monotonic()-started,'usage':usage,'answers':core.render_systemone(qs,answers)}
            receipt['semantic_pass']=(receipt['answers']['damaged']['noul']>.5 and receipt['answers']['color']['choice']=='red'
                                      and receipt['answers']['damage']['score']>.5)
            try:core.decisions_usage(usage);receipt['strict_decisions_usage']=True
            except Reject as error:receipt['strict_decisions_usage']=False;receipt['usage_gap']=error.code
            receipts.append(receipt)
        body={'model':'gpt-6-luna','input':[{'role':'user','content':[{'type':'input_image',
              'image_url':'data:image/png;base64,'+base64.b64encode(red_png()).decode(),'detail':'original'}]}],
              'questions':[{'type':'choice','instructions':'What is the image color?','choices':[{'value':'red'},{'value':'blue'}]}]}
        state,qs,meta=core.parse_decisions(body)
        result['active_smoke']={'kind':'inline_image'}
        started=time.monotonic();answers,usage,_=core.decide(state,qs,context=RequestContext(deadline))
        receipt={'kind':'inline_image','seconds':time.monotonic()-started,'usage':usage,'answers':core.render_decisions(meta,answers)}
        receipt['semantic_pass']=receipt['answers'][0].get('choice')=='red'
        try:core.decisions_usage(usage);receipt['strict_decisions_usage']=True
        except Reject as error:receipt['strict_decisions_usage']=False;receipt['usage_gap']=error.code
        receipts.append(receipt)
        result.pop('active_smoke',None)
        result['inference_gate']=all(r['semantic_pass'] for r in receipts)
        result['strict_decisions_usage_gate']=all(r['strict_decisions_usage'] for r in receipts)
        result['ok']=target_ready and result['inference_gate'] and result['strict_decisions_usage_gate']
        result['capability_gate']='passed' if result['ok'] else 'incomplete'
    except Exception as error:
        result['error']={'code':error.code,'message':error.message} if isinstance(error,Reject) else {'code':type(error).__name__,'message':'Local login or capability check failed'}
        if isinstance(error,Reject) and hasattr(error,'accounting'):result['failure_accounting']=error.accounting
        result['capability_gate']='failed'
    return result
