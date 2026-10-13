"""OAuth refresh attempts, stream/ledger accounting, local key safety and outward diagnostics across real stream and HTTP boundaries."""
import asyncio
import contextlib
import io
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from jev_luna import core as d, subscription_auth as auth
from jev_luna.client import request, DecisionError
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext, bind, attempt_route
from jev_luna.local import service_key
from jev_luna.transport import Route, provider_error, prepare
from test_http_service import Base, OPEN, Up, tool_reply

BODY={'model':'gpt-6-luna','state':'The switch is on.','questions':{'on':{'type':'noul','instructions':'Is the switch on?'}}}
USAGE={'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}


class OAuthAttempts(unittest.TestCase):
    def test_named_default_and_repeated_401_have_one_native_refresh(self):
        state,questions=d.parse_systemone(BODY)
        success=({'q0':{'p_true':.9}},USAGE)
        for body in ({'error':{'code':'token_expired'}},{'error':{}},{}):
            with self.subTest(body=body):
                error=provider_error(401,body)
                for effects,expected in (([error,success],True),([error,error],False)):
                    with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth',return_value=Route('https://chatgpt.com/responses',{},auth_generation=(1,2,3))),patch.object(d,'upstream_call',side_effect=effects),patch.object(auth,'refresh') as refresh:
                        if expected:self.assertEqual(d._infer_batch(state,questions,time.monotonic()+3)[0],[{'p':.9}])
                        else:
                            with self.assertRaises(Reject):d._infer_batch(state,questions,time.monotonic()+3)
                    refresh.assert_called_once()
                    self.assertEqual(refresh.call_args.kwargs['expected_generation'],(1,2,3))

    def test_permanent_auth_rejection_never_refreshes(self):
        state,questions=d.parse_systemone(BODY)
        error=provider_error(401,{'error':{'code':'subscription_sharing_invalid_user'}})
        with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth'),patch.object(d,'upstream_call',side_effect=error),patch.object(auth,'refresh') as refresh:
            with self.assertRaises(Reject):d._infer_batch(state,questions,time.monotonic()+2)
        refresh.assert_not_called()

    def test_refresh_does_not_mutate_another_inflight_attempt_route(self):
        context=RequestContext(time.monotonic()+5);paused=threading.Event();rotated=threading.Event();generation=[1];routes=[]
        def prepare_route(ctx):return Route('https://chatgpt.com/responses',{'Authorization':'fixture-'+str(generation[0])},auth_generation=(generation[0],2,3))
        def call(payload,timeout):
            route=attempt_route.get();routes.append((threading.current_thread().name,route))
            if threading.current_thread().name=='route-B':
                paused.set();self.assertTrue(rotated.wait(2))
                self.assertIs(route,attempt_route.get());self.assertEqual(route.auth_generation,(1,2,3))
                self.assertEqual(route.headers['Authorization'],'fixture-1')
            elif route.auth_generation[0]==1:
                self.assertTrue(paused.wait(2));raise provider_error(401,{'error':{'code':'token_expired'}})
            return {'q0':{'p_true':.9}},USAGE
        def refresh(*args,**kw):generation[0]=2;rotated.set()
        state,questions=d.parse_systemone(BODY);results=[];errors=[]
        def worker():
            try:
                with bind(context):results.append(d._infer_batch(state,questions,context.deadline))
            except BaseException as error:errors.append(error)
        with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth',side_effect=prepare_route),patch.object(d,'upstream_call',side_effect=call),patch.object(auth,'refresh',side_effect=refresh):
            b=threading.Thread(target=worker,name='route-B');a=threading.Thread(target=worker,name='route-A');b.start();a.start();b.join(3);a.join(3)
        self.assertFalse(a.is_alive() or b.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(results),2)
        self.assertEqual([route.auth_generation[0] for name,route in routes if name=='route-A'],[1,2])
        with self.assertRaises(dataclasses.FrozenInstanceError):routes[0][1].auth_generation=None
        with self.assertRaises(TypeError):routes[0][1].headers['Authorization']='changed'

    def test_account_change_is_rejected_with_native_generation_bound_to_route(self):
        context=RequestContext(time.monotonic()+2)
        with patch.object(auth,'_credentials',side_effect=[('fixture','one',('native','before','NO_CONSTRAINT')),('other','two',('native','after','NO_CONSTRAINT'))]),patch.object(auth,'getproxies',return_value={}):
            route=prepare({'transport':'subscription'},context)
            self.assertEqual(route.auth_generation,('native','before','NO_CONSTRAINT'))
            with self.assertRaises(Reject) as raised:prepare({'transport':'subscription'},context)
        self.assertEqual(raised.exception.code,'account_changed')


class StreamAndLedger(Base):
    def response(self):return OPEN.open(urllib.request.Request(self.url+'/v1/systemone',json.dumps(BODY).encode(),{'Content-Type':'application/json'}))

    def test_top_level_rate_limit_is_429_with_safe_metadata(self):
        event={'type':'error','code':'rate_limit_exceeded','message':'private provider detail','param':'input','sequence_number':1}
        Up.reply=lambda _: (200,'data: '+json.dumps(event)+'\n\n',{'Retry-After':'7','x-request-id':'fixture-id'})
        try:self.response();self.fail('Expected stream error')
        except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as error:
            with error:
                self.assertEqual(error.code,429);self.assertEqual(error.headers['Retry-After'],'7');self.assertEqual(error.headers['X-Upstream-Request-Id'],'fixture-id')
                payload=json.load(error);self.assertEqual(payload['error']['code'],'rate_limit_exceeded');self.assertEqual(payload['error']['param'],'input')
                self.assertEqual(payload['unknown_usage_attempts'],1)
        self.assertEqual(len(Up.seen),1)

    def test_nested_failed_and_incomplete_streams_preserve_usage(self):
        for kind,code in (('response.failed','model_not_found'),('response.incomplete','incomplete_response')):
            failed={'error':{'code':code},'usage':USAGE}
            Up.reply=lambda _,kind=kind: (200,'data: '+json.dumps({'type':kind,'response':failed})+'\n\n')
            status,payload=self.post('/v1/systemone',BODY)
            self.assertEqual(payload['error']['code'],code);self.assertEqual(payload['observed_usage']['input_tokens'],100)
            self.assertEqual(payload['unknown_usage_attempts'],0)

    def test_unknown_attempt_then_success_is_visible_without_changing_json(self):
        Up.reply=lambda _: (502,{'error':{}}) if len(Up.seen)==1 else tool_reply({'q0':{'p_true':.9}})
        with self.response() as response:
            self.assertEqual(response.headers['X-Decide-D-Usage-Status'],'partial');self.assertEqual(response.headers['X-Decide-D-Unknown-Usage-Attempts'],'1')
            payload=json.load(response);self.assertEqual(set(payload),{'model','answers','usage'});self.assertEqual(payload['usage']['input_tokens'],100)

    def test_completed_invalid_readout_then_success_counts_both_observations(self):
        Up.reply=lambda _: tool_reply({'q0':[0,0]}) if len(Up.seen)==1 else tool_reply({'q0':{'p_true':.9}})
        with self.response() as response:
            self.assertEqual(response.headers['X-Decide-D-Usage-Status'],'observed');self.assertEqual(response.headers['X-Decide-D-Unknown-Usage-Attempts'],'0')
            self.assertEqual(json.load(response)['usage']['input_tokens'],200)

    def test_client_keeps_error_accounting_and_success_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'JEV_LUNA_HOME':directory}):
            service_key(create=True)
            Up.reply=lambda _: (502,{'error':{}}) if len(Up.seen)==1 else tool_reply({'q0':{'p_true':.9}})
            result=asyncio.run(request(BODY,url=self.url))
            self.assertEqual(result.diagnostics,{'usage_status':'partial','unknown_usage_attempts':1})
            Up.reply=lambda _: (403,{'error':{'code':'account_disabled'},'usage':USAGE},{'Retry-After':'2'})
            with self.assertRaises(DecisionError) as raised:asyncio.run(request(BODY,url=self.url))
            self.assertIsInstance(raised.exception,ValueError);self.assertEqual(raised.exception.error['code'],'account_disabled')
            self.assertEqual(raised.exception.accounting['observed_usage']['input_tokens'],100);self.assertEqual(raised.exception.headers['Retry-After'],'2')


class LocalKey(unittest.TestCase):
    def test_fifo_symlink_oversize_and_growing_key_fail_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'JEV_LUNA_HOME':directory}):
            path=Path(directory)/'service.key'
            for kind in ('fifo','symlink','large'):
                if kind=='fifo' and hasattr(os,'mkfifo'):os.mkfifo(path,0o600)
                elif kind=='symlink':path.symlink_to(Path(directory)/'missing')
                else:path.write_bytes(b'x'*1025);path.chmod(0o600)
                started=time.monotonic()
                with self.assertRaises(ValueError):service_key()
                self.assertLess(time.monotonic()-started,.1);path.unlink()
            value=service_key(create=True);self.assertEqual(service_key(),value)


class OutwardDiagnostics(unittest.TestCase):
    def test_cli_keeps_json_stdout_and_partial_usage_stderr(self):
        from jev_luna import cli
        from jev_luna.client import DecisionResult
        async def result(*args,**kwargs):return DecisionResult({'model':'gpt-6-luna','answers':{},'usage':{}},{'X-Decide-D-Usage-Status':'partial','X-Decide-D-Unknown-Usage-Attempts':'1'})
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch('sys.argv',['jev-luna','decide']),patch('sys.stdin',io.TextIOWrapper(io.BytesIO(json.dumps(BODY).encode()))),patch('jev_luna.client.request',side_effect=result),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(),0)
        self.assertEqual(set(json.loads(stdout.getvalue())),{'model','answers','usage'})
        self.assertEqual(json.loads(stderr.getvalue())['decision_diagnostics']['unknown_usage_attempts'],1)

    def test_mcp_failure_preserves_structured_accounting(self):
        from jev_luna import mcp
        error=DecisionError(429,{'error':{'code':'rate_limit_exceeded'},'observed_usage':{'input_tokens':7},'usage_status':'partial','unknown_usage_attempts':1},{'Retry-After':'2'})
        with patch.object(mcp,'request',side_effect=error):
            with self.assertRaises(ValueError) as caught:asyncio.run(mcp.jev_decide('x',BODY['questions']))
        result=json.loads(str(caught.exception));self.assertEqual(result['observed_usage']['input_tokens'],7)
        self.assertEqual(result['unknown_usage_attempts'],1);self.assertEqual(result['headers']['Retry-After'],'2')
