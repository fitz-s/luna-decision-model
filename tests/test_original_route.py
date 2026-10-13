"""Prevent the local subscription replacement from growing a separate app-registration flow."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from jev_luna import cli, subscription_auth as auth


class OriginalRoute(unittest.TestCase):
    def test_doctor_keeps_completed_smokes_and_observed_failure_usage(self):
        from jev_luna import core,doctor
        from jev_luna.errors import Reject
        _,questions=core.parse_systemone({'model':'gpt-6-luna','state':'x','questions':{
            'damaged':{'type':'noul','instructions':'damaged?'},
            'color':{'type':'choice','instructions':'color?','criteria':{'red':'red','blue':'blue'}},
            'damage':{'type':'score','instructions':'damage?','criteria':['intact','damaged']}}})
        answers=[core.typed(q,v) for q,v in zip(questions,([1,0],[1,0],[0,1]))]
        usage={'input_tokens':10,'output_tokens':2,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}
        failure=Reject(504,'deadline','request deadline exceeded')
        failure.accounting={'observed_usage':{'input_tokens':7,'output_tokens':1},'usage_status':'partial','unknown_usage_attempts':1}
        with patch.object(core,'TRANSPORT','subscription'),patch.object(auth,'connection',return_value=(MagicMock(),'/responses',{})),patch.object(core,'decide',side_effect=[(answers,usage,'disabled'),failure]):
            result=doctor.check(live=True)
        self.assertFalse(result['ok']);self.assertEqual(len(result['receipts']),1)
        self.assertTrue(result['receipts'][0]['semantic_pass'])
        self.assertEqual(result['active_smoke']['reasoning_effort'],'medium')
        self.assertEqual(result['failure_accounting'],failure.accounting)

    def test_existing_login_is_reused_without_launching_an_authorization_flow(self):
        connection=MagicMock()
        out=io.StringIO()
        with patch.object(auth,'connection',return_value=(connection,'/responses',{})),patch.object(cli.subprocess,'run') as launch,patch('sys.argv',['jev-luna','login']),contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(),0)
        launch.assert_not_called()
        connection.close.assert_called_once()
        self.assertEqual(json.loads(out.getvalue()),{'login':'existing_codex_chatgpt','new_registration':False})

    def test_missing_login_uses_codex_oauth_and_removes_paid_api_key_environment(self):
        connection=MagicMock()
        out=io.StringIO()
        with patch.object(auth,'connection',side_effect=[auth.CodexLoginRequired('Codex ChatGPT login is required'),(connection,'/responses',{})]),patch.object(cli.subprocess,'run',return_value=MagicMock(returncode=0)) as launch,patch.dict(os.environ,{'OPENAI_API_KEY':'unit-test-not-a-key'}),patch('sys.argv',['jev-luna','login']),contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(),0)
        self.assertEqual(launch.call_args.args[0],['codex','login'])
        self.assertNotIn('OPENAI_API_KEY',launch.call_args.kwargs['env'])
        self.assertFalse(json.loads(out.getvalue())['new_registration'])

    def test_legacy_proxy_settings_fail_explicitly(self):
        env={key:value for key,value in os.environ.items() if key!='DECIDE_D_TRANSPORT'}
        env.update(DECIDE_D_UPSTREAM='127.0.0.1:1',DECIDE_D_RESPONSES_URL='http://127.0.0.1:2/responses')
        result=subprocess.run([sys.executable,'-c','import jev_luna.core'],env=env,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('remove legacy proxy settings',result.stderr)

    def test_removed_registration_module_is_not_an_installable_entry_point(self):
        root=Path(cli.__file__).parent
        self.assertFalse((root/'oauth.py').exists())
        self.assertNotIn('dynamic_agent_client',''.join(path.read_text() for path in root.glob('*.py')))
