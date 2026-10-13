"""Native OAuth refresh coordination, using isolated metadata and fake manager work."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from jev_luna import subscription_auth as auth
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext


class NativeRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'JEV_LUNA_HOME':self.tmp.name});self.env.start()
        auth._refreshes.clear();self.auth_path=patch.object(auth,'auth_file',return_value=__import__('pathlib').Path(self.tmp.name)/'auth.json');self.auth_path.start()
    def tearDown(self):self.auth_path.stop();self.env.stop();self.tmp.cleanup()

    def test_native_refresh_is_shared_and_completes_after_one_caller_cancels(self):
        started,finish=threading.Event(),threading.Event();calls=[]
        def native(timeout, **kwargs):calls.append(timeout);started.set();self.assertTrue(finish.wait(2))
        contexts=[RequestContext(time.monotonic()+3) for _ in range(4)]
        errors=[]
        def request(context):
            try:auth.refresh(3,expected_generation=('native','same'),context=context)
            except Reject as error:errors.append(error.code)
        with patch.object(auth,'_native_login',return_value=('access','account',('native','same'))),patch.object(auth,'_refresh_native',side_effect=native),patch.object(auth,'connection') as connection:
            threads=[threading.Thread(target=request,args=(ctx,)) for ctx in contexts]
            for thread in threads:thread.start()
            self.assertTrue(started.wait(1));contexts[0].cancel();finish.set()
            for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
            connection.assert_not_called()
        self.assertEqual(len(calls),1);self.assertEqual(errors,['cancelled'])

    def test_generation_and_owner_are_forwarded_to_one_native_session(self):
        context=RequestContext(time.monotonic()+1)
        context.auth_registration='account'
        with patch.object(auth,'_native_login') as native:
            auth.refresh(1,expected_generation=('native','same','NO_CONSTRAINT'),context=context)
        native.assert_called_once()
        args=native.call_args.kwargs
        self.assertTrue(args['refresh'])
        self.assertEqual(args['expected_account'],'account')
        self.assertEqual(args['expected_generation'],('native','same','NO_CONSTRAINT'))

    def test_waiting_caller_keeps_original_deadline_without_interrupting_persistence(self):
        started,finish=threading.Event(),threading.Event()
        def native(timeout, **kwargs):started.set();finish.wait(2)
        with patch.object(auth,'_native_login',return_value=('access','account',('native','same'))),patch.object(auth,'_refresh_native',side_effect=native):
            started_at=time.monotonic()
            with self.assertRaises(Reject) as raised:auth.refresh(.08,expected_generation=('native','same'))
            self.assertTrue(started.is_set());self.assertEqual(raised.exception.code,'deadline')
            self.assertLess(time.monotonic()-started_at,.2)
            finish.set()
            flight=next(iter(auth._refreshes.values()));self.assertTrue(flight['event'].wait(1))
