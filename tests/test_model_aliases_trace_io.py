"""Model alias payload identity, score label handling and slow-disk trace I/O checks."""
import json,time,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from jev_luna import core
from jev_luna.receipts import AtomicReceipt,RequestTrace,flush_traces
class ContractTests(unittest.TestCase):
    def test_public_aliases_do_not_change_model_payload(self):
        body={'model':'gpt-6-luna','state':'fixture','questions':{'q':{'type':'noul','instructions':'True?'}}}
        state,qs=core.parse_systemone(body);reference=core.build_upstream(state,qs)
        for alias in ('jev-latest','jev-preview','jev-1.13-20260917','typesafe/jev-1.13-20260917'):
            state,qs=core.parse_systemone({**body,'model':alias})
            self.assertEqual(core.build_upstream(state,qs),reference)
        with self.assertRaises(core.Reject):core.parse_systemone({**body,'model':'other-model'})

    def test_score_label_and_description_are_not_conflated(self):
        body={'model':'gpt-6-luna','input':'fixture','questions':[{'type':'score','instructions':'Use the exact label.',
            'levels':[{'label':'a: b','description':'c'},{'label':'a','description':'b: c'}]}]}
        state,qs,meta=core.parse_decisions(body)
        self.assertNotEqual(qs[0]['opts'][0]['desc'],qs[0]['opts'][1]['desc'])
        self.assertEqual(qs[0]['opts'][0]['desc'],{'label':'a: b','description':'c'})
        original=json.loads(core.build_upstream(state,qs)['input'][-1]['content'][0]['text'].split('\n',1)[1])
        self.assertEqual([o['description']['label'] for o in original[0]['outcomes']],['a: b','a'])

class TraceTests(unittest.TestCase):
    def test_slow_diagnostic_disk_cannot_hold_request_context_lock(self):
        from jev_luna.lifecycle import RequestContext
        entered=threading.Event();release=threading.Event()
        original=AtomicReceipt._publish
        def slow(self,*args,**kwargs):
            entered.set();release.wait(2);return original(self,*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(AtomicReceipt,'_publish',slow):
                trace=RequestTrace(directory,'slow-fixture',{})
                ctx=RequestContext(time.monotonic()+1,observer=trace)
                self.assertTrue(entered.wait(1))
                start=time.monotonic();ctx.event('queue',wait_seconds=0);ctx.cancel()
                self.assertLess(time.monotonic()-start,.2)
                self.assertFalse(flush_traces(.01))
                ctx.event('finished',accounting=ctx.snapshot())
                release.set();self.assertTrue(flush_traces(3))
            data=json.loads((Path(directory)/'slow-fixture.json').read_text())
            self.assertTrue(data['diagnostics_complete']);self.assertEqual(data['phase'],'finished')

    def test_overflow_is_not_a_complete_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            trace=RequestTrace(directory,'overflow-fixture',{})
            with patch('jev_luna.receipts._MAX_EVENT_BYTES',2):trace('oversized',{'long':'metadata'})
            trace('finished',{})
            self.assertTrue(flush_traces(3))
            data=json.loads((Path(directory)/'overflow-fixture.json').read_text())
            self.assertFalse(data['diagnostics_complete']);self.assertGreater(data['dropped_events'],0)
