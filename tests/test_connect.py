"""Native login connection flow: reuse, explicit login, port preservation and local-only credentials."""
import json
import socket
import unittest
from unittest.mock import patch, Mock

from jev_luna import connect
from jev_luna.subscription_auth import CodexLoginRequired, NativeAuthStateError


class Connect(unittest.TestCase):
    def test_existing_login_never_invokes_login(self):
        with patch('jev_luna.subscription_auth.request_parameters', return_value=('', {}, (), None)), \
             patch.object(connect.subprocess, 'run') as run:
            self.assertEqual(connect.login_existing(allow_login=True), 'existing_codex_chatgpt')
            run.assert_not_called()

    def test_fault_and_policy_never_start_unrelated_login(self):
        for failure in (NativeAuthStateError('fault'), ValueError('bad routing')):
            with patch('jev_luna.subscription_auth.request_parameters', side_effect=failure), \
                 patch.object(connect.subprocess, 'run') as run:
                with self.assertRaises(ValueError): connect.login_existing(allow_login=True)
                run.assert_not_called()

    def test_missing_login_requires_flag_and_native_confirmation(self):
        with patch('jev_luna.subscription_auth.request_parameters', side_effect=CodexLoginRequired()), \
             patch.object(connect.subprocess, 'run') as run:
            with self.assertRaises(ValueError): connect.login_existing()
            run.assert_not_called()
        with patch('jev_luna.subscription_auth.request_parameters', side_effect=[CodexLoginRequired(), ('', {}, (), None)]), \
             patch.object(connect.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(connect.login_existing(allow_login=True), 'codex_chatgpt')
            self.assertEqual(run.call_args.args[0], ['codex', 'login'])
            self.assertNotIn('OPENAI_API_KEY', run.call_args.kwargs['env'])

    def test_occupied_port_is_preserved_before_auth(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            with patch.object(connect, 'login_existing') as login:
                with self.assertRaisesRegex(ValueError, 'occupied'):
                    connect.start('127.0.0.1', listener.getsockname()[1], allow_login=True)
                login.assert_not_called()

    def test_sdk_loopback_and_local_credential_only(self):
        for url in ('https://api.openai.com/v1', 'http://127.0.0.1:2/v1?a=b', 'http://user@localhost:2/v1', 'http://localhost/v1'):
            with self.assertRaises(ValueError): connect.openai_client(base_url=url)
        with self.assertRaises(ValueError): connect.openai_client(api_key='external')
        with patch.object(connect, 'service_key', return_value='private-fixture'), patch('openai.OpenAI') as sdk:
            custom = object()
            connect.openai_client(base_url='http://127.0.0.1:20299/v1', timeout=7, max_retries=0, http_client=custom)
            self.assertEqual(sdk.call_args.kwargs, {'api_key': 'private-fixture', 'base_url': 'http://127.0.0.1:20299/v1',
                                                   'timeout': 7, 'max_retries': 0, 'http_client': custom})
        with patch.object(connect, 'service_key', return_value='private-fixture'), patch('openai.OpenAI') as sdk, \
             patch('openai.DefaultHttpxClient') as http:
            connect.openai_client(); http.assert_called_once_with(trust_env=False)
            self.assertIs(sdk.call_args.kwargs['http_client'], http.return_value)

    def test_configuration_no_credentials(self):
        with patch.object(connect, 'service_key') as key:
            result = connect.configuration('::1', 20299)
            self.assertEqual(result['openai_base_url'], 'http://[::1]:20299/v1')
            self.assertIn("openai_client(base_url='http://[::1]:20299/v1')", result['python'])
            self.assertNotIn('Authorization', json.dumps(result))
            key.assert_not_called()
