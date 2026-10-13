import json
import io
import queue
import time
import unittest
from unittest.mock import MagicMock, patch
from jev_luna import subscription_auth as a
from jev_luna import core as d
from test_http_service import Base


class SubscriptionAuth(unittest.TestCase):
    def test_credentials_are_sent_only_inside_official_https_connection(self):
        with patch.object(a,'_credentials',return_value=('TEST_ACCESS','TEST_ACCOUNT',('native','hash','NO_CONSTRAINT'))),patch.object(a,'getproxies',return_value={}),patch.object(a.http.client,'HTTPSConnection') as connect:
            conn,path,headers=a.connection(5)
            connect.assert_called_once_with('chatgpt.com',timeout=5)
            self.assertEqual(path,'/backend-api/codex/responses')
            self.assertEqual(headers['Authorization'],'Bearer TEST_ACCESS')
            self.assertEqual(conn._jev_auth_generation,('native','hash','NO_CONSTRAINT'))

    def test_http_connect_keeps_tls_target_on_the_official_host(self):
        with patch.object(a,'_credentials',return_value=('TEST_ACCESS','TEST_ACCOUNT',('native','hash','NO_CONSTRAINT'))),patch.object(a,'getproxies',return_value={'https':'http://127.0.0.1:20128'}),patch.object(a.http.client,'HTTPSConnection') as connect:
            conn,_,_=a.connection(5)
            connect.assert_called_once_with('127.0.0.1',20128,timeout=5)
            conn.set_tunnel.assert_called_once_with('chatgpt.com',443)

    def test_refresh_is_delegated_to_same_native_owner(self):
        with patch.object(a,'_native_login') as native:a._refresh_native(1)
        native.assert_called_once_with(1,refresh=True,expected_generation=None,expected_account=None)


class SubscriptionRenewal(Base):
    def request_body(self):
        return {'model':'gpt-6-luna','state':'A refund was requested.',
                'questions':{'billing':{'type':'noul','instructions':'Is this about billing?'}}}

    def test_malformed_login_is_a_json_401(self):
        from jev_luna.transport import prepare
        with patch.object(d,'prepare_auth',side_effect=lambda context: prepare({'transport':'subscription'},context)),patch.object(a,'_credentials',side_effect=a.CodexLoginRequired('Missing login')):
            status,result=self.post('/v1/systemone',self.request_body())
        self.assertEqual((status,result['error']['code']),(401,'subscription_auth'))

    def test_first_401_and_401_after_transient_failure_renew_once(self):
        body=self.request_body();state,qs=d.parse_systemone(body)
        success=({'q0':{'p_true':.9}},{'input_tokens':100,'output_tokens':20})
        for failures in ([401],[502,401],[502,502,401]):
            effects=[d.Reject(status,'upstream_failure','test failure',retryable=status==502) for status in failures]+[success]
            with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth'),patch.object(d,'upstream_call',side_effect=effects) as call,patch.object(a,'refresh') as renew:
                answers,usage=d._infer_batch(state,qs,time.monotonic()+5)
            self.assertEqual(answers,[{'p':.9}]);self.assertEqual(usage['input_tokens'],100)
            self.assertEqual(call.call_count,len(effects));renew.assert_called_once()

    def test_renewal_failure_is_explicit_and_not_retried(self):
        state,qs=d.parse_systemone(self.request_body())
        with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth'),patch.object(d,'upstream_call',side_effect=d.Reject(401,'upstream_failure','test failure')) as call,patch.object(a,'refresh',side_effect=ValueError('test renewal failure')) as renew:
            with self.assertRaises(d.Reject) as error:d._infer_batch(state,qs,time.monotonic()+5)
        self.assertEqual((error.exception.status,error.exception.code),(401,'subscription_auth'))
        self.assertEqual(call.call_count,1);renew.assert_called_once()

    def test_second_401_cannot_trigger_repeated_renewal(self):
        state,qs=d.parse_systemone(self.request_body())
        with patch.object(d,'TRANSPORT','subscription'),patch.object(d,'prepare_auth'),patch.object(d,'upstream_call',side_effect=d.Reject(401,'upstream_failure','test failure')) as call,patch.object(a,'refresh') as renew:
            with self.assertRaises(d.Reject):d._infer_batch(state,qs,time.monotonic()+5)
        self.assertEqual(call.call_count,2);renew.assert_called_once()
