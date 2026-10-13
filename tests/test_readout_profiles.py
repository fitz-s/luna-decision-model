"""Readout profile representations, restoration and validation; local only, no auth or model calls."""
import copy
import json
import unittest
import time
from unittest.mock import patch, Mock
from jsonschema import validate, ValidationError
from jev_luna import core, readout_profiles as rp
from jev_luna.lifecycle import RequestContext, bind
from jev_luna.transport import Route


def request(n=3, state='evidence'):
    return core.parse_systemone({'model':'gpt-6-luna','state':state,'questions':{
        'p':{'type':'noul','instructions':'Supported?'},
        'c':{'type':'choice','instructions':'Choose.','criteria':{f'id{i}':f'label{i}' for i in range(n)}},
        's':{'type':'score','instructions':'Score.','criteria':['low','high']}}})


class Profiles(unittest.TestCase):
    def base(self,n=3,state='evidence'):
        st,qs=request(n,state)
        with patch.object(core,'READOUT_PROFILE','baseline'):
            return core.build_upstream(st,qs),qs
    def test_default_is_identity(self):
        p,qs=self.base();before=json.dumps(p).encode()
        self.assertIs(rp.apply(p,qs,'baseline'),p)
        self.assertEqual(json.dumps(p).encode(),before)
        with self.assertRaises(ValueError):rp.validate_profile('unknown')

    def test_invalid_completed_profile_readout_keeps_regeneration_and_usage(self):
        for name in ('compact-weight-v1', 'compact-probability-v1', 'probability-v1'):
            st, qs = request()
            if rp.is_compact(name):
                invalid = {'q0': .75, 'q2': [.3, .7]}  # Missing head must retain the old retry behavior.
                valid = {'q0': .75, 'q1': [.2, .3, .5], 'q2': [.3, .7]}
                reason = 'question_ids'
            else:
                valid = {'q0': {'refused': False, 'answer': {'p_true': .75}},
                         'q1': {'refused': False, 'answer': [.2, .3, .5]},
                         'q2': {'refused': False, 'answer': [.3, .7]}}
                invalid = copy.deepcopy(valid); invalid['q1']['answer'] = [2, 0, 0]
                reason = 'invalid_number'
            usage = {'input_tokens': 10, 'output_tokens': 2}
            def completed(report):
                return ({'status': 'completed', 'model': 'gpt-6-luna', 'usage': usage,
                         'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(report)}]}]},
                        {}, dict(usage))
            fixture = Mock(); fixture.call.side_effect = [completed(invalid), completed(valid)]
            events = []
            context = RequestContext(time.monotonic()+3, observer=lambda event, fields: events.append((event, fields)))
            route = Route('https://chatgpt.com/backend-api/codex/responses', {}, profile='subscription')
            with self.subTest(profile=name), patch.object(core, 'READOUT_PROFILE', name), \
                 patch.object(core, 'prepare_auth', return_value=route) as fresh_lookup, \
                 patch('jev_luna.transport.client', return_value=fixture), bind(context):
                answers, observed = core._infer_batch(st, qs, context.deadline)
                self.assertEqual(len(answers), 3)
                self.assertEqual(fixture.call.call_count, 2)
                self.assertEqual(fresh_lookup.call_count, 2)
                self.assertEqual(observed['input_tokens'], 20)
                self.assertEqual(observed['output_tokens'], 4)
                self.assertEqual(context.snapshot()['unknown_usage_attempts'], 0)
                self.assertTrue(any(event == 'upstream_readout_rejected' and fields['reason'] == reason
                                    for event, fields in events))
    def test_inputs_outcomes_order_and_effort_policy_preserved(self):
        for n in (1,16,17,255):
            for state in ('short','x'*8500):
                base,qs=self.base(n,state);saved=copy.deepcopy(base)
                for name in rp.PROFILES-{'baseline'}:
                    p=rp.apply(base,qs,name)
                    self.assertEqual(p['input'][:-1],base['input'][:-1])
                    for key in ('model','reasoning','max_output_tokens','store','stream'):
                        self.assertEqual(p[key],base[key])
                    a=json.loads(base['input'][-1]['content'][0]['text'].split('\n',1)[1])
                    b=json.loads(p['input'][-1]['content'][0]['text'].split('\n',1)[1])
                    for x,y in zip(a,b):
                        if isinstance(y['outcomes'], dict):
                            y['outcomes'] = [o for block in y['outcomes'].values() for o in block]
                        if rp.uses_keyed_ids(name) and len(y['outcomes']) > 16:
                            for previous, changed in zip(x['outcomes'],y['outcomes']):
                                changed['id'] = previous['id']
                        self.assertEqual({k:v for k,v in x.items() if k!='how_to_answer'},
                                         {k:v for k,v in y.items() if k!='how_to_answer'})
                self.assertEqual(base,saved)
    def test_compact_restores_full_vectors_and_refusal(self):
        base,qs=self.base();p=rp.apply(base,qs,'compact-probability-v1');sc=p['text']['format']['schema']
        raw={'q0':.75,'q1':[.1,.2,.7],'q2':[.3,.7]};validate(raw,sc)
        e=rp.restore_envelopes(raw,sc)
        self.assertEqual(core.vector(qs[0],e['q0']['answer']),[.75,.25])
        self.assertEqual(core.vector(qs[1],e['q1']['answer']),[.1,.2,.7])
        self.assertAlmostEqual(core.typed(qs[2],core.vector(qs[2],e['q2']['answer']))['score'],.7)
        raw['q1']=None;validate(raw,sc);self.assertTrue(rp.restore_envelopes(raw,sc)['q1']['refused'])
        for val in ([1,0],[1,0,0,0],[True,0,0]):
            with self.assertRaises(ValidationError):validate({**raw,'q1':val},sc)
            with self.assertRaises(ValueError):rp.restore_envelopes({**raw,'q1':val},sc)
    def test_named_long_vector_and_singleton_not_omitted(self):
        for n in (1,17,255):
            base,qs=self.base(n);p=rp.apply(base,qs,'compact-weight-v1');sc=p['text']['format']['schema']
            val=[1] if n==1 else {f'id{i}':1 for i in range(n)}
            raw={'q0':.5,'q1':val,'q2':[1,1]};validate(raw,sc)
            e=rp.restore_envelopes(raw,sc);self.assertEqual(len(core.vector(qs[1],e['q1']['answer'])),n)
            if n>1:
                raw['q1'].pop('id0')
                with self.assertRaises(ValidationError):validate(raw,sc)
                with self.assertRaises(ValueError):rp.restore_envelopes(raw,sc)
    def test_probability_validation_is_not_only_provider_schema(self):
        for name in ('probability-v1','compact-probability-v1'):
            for value in ([2,0], [True,0], [float('nan'),0], [10**1000,0]):
                with self.assertRaises(ValueError):
                    rp.validate_probabilities({'q0':{'refused':False,'answer':value}},name)
            report={'q0':{'refused':False,'answer':[.2,.8]}, 'q1':{'refused':True,'answer':None}}
            before=copy.deepcopy(report);rp.validate_probabilities(report,name);self.assertEqual(report,before)
        base,qs=self.base();sc=rp.apply(base,qs,'compact-probability-v1')['text']['format']['schema']
        with self.assertRaises(ValueError):rp.restore_envelopes({'q0':10**1000,'q1':[.2,.3,.5],'q2':[.1,.9]},sc)

    def test_zero_mass_not_silently_repaired(self):
        _,qs=self.base()
        with self.assertRaises(ValueError):core.vector(qs[1],[0,0,0])
        self.assertEqual(core.vector(qs[1],[1,0,0]),[1,0,0])
        self.assertEqual(core.conf_choice([1,0,0]),1)


class WeightProfiles(unittest.TestCase):
    def test_keyed_names_keep_complete_semantics_and_exact_mapping(self):
        for n in (1,16,17,77,255):
            st,qs=core.parse_systemone({'model':'gpt-6-luna','state':'evidence','questions':{
                'q':{'type':'choice','criteria':{f'original_label_{i}':f'description {i}'for i in range(n)}}}})
            with patch.object(core,'READOUT_PROFILE','baseline'):base=core.build_upstream(st,qs)
            payload=rp.apply(base,qs,'keyed-weight-v1');schema=payload['text']['format']['schema']
            values=[1]if n==1 else [i+1 for i in range(n)]
            answer=values if n<=16 else {f'o{i}':v for i,v in enumerate(values)}
            validate({'q0':answer},schema)
            e=rp.restore_envelopes({'q0':answer},schema)
            with patch.object(core,'READOUT_PROFILE','keyed-weight-v1'):v=core.vector(qs[0],e['q0']['answer'])
            self.assertEqual(v.index(max(v)),n-1)
            if n>16:
                self.assertEqual(rp.restore_keys(qs[0],answer),{o['tid']:v for o,v in zip(qs[0]['opts'],values)})
                with self.assertRaises(ValueError):rp.restore_keys(qs[0],{**answer,'other':1})
                with self.assertRaises(ValueError):rp.restore_keys(qs[0],dict(list(answer.items())[1:]))
            before=json.loads(base['input'][-1]['content'][0]['text'].split('\n',1)[1])[0]
            after=json.loads(payload['input'][-1]['content'][0]['text'].split('\n',1)[1])[0]
            self.assertEqual([(o['key'],o.get('description'))for o in before['outcomes']],
                             [(o['key'],o.get('description'))for o in after['outcomes']])
            self.assertEqual(base['input'][:-1],payload['input'][:-1])
            self.assertEqual(base['reasoning'],payload['reasoning'])

    def test_full_boundary_vectors_and_mapping(self):
        for n in (1, 16, 17, 32, 33, 77, 255):
            st, qs = core.parse_systemone({'model': 'gpt-6-luna', 'state': {'numbers': [1, 2]},
                'questions': {'q': {'type': 'choice', 'instructions': 'Match evidence.',
                    'criteria': {f'选项 {i}': f'description {i}' for i in range(n)}}}})
            with patch.object(core, 'READOUT_PROFILE', 'baseline'):
                base = core.build_upstream(st, qs)
            payload = rp.apply(base, qs, 'blocked-weight-v1')
            schema = payload['text']['format']['schema']
            values = [i + 1 for i in range(n)]
            answer = values if n <= 16 else {f'b{i//16}': values[i:i+16] for i in range(0, n, 16)}
            if n == 1: answer = [1]
            raw = {'q0': answer}
            validate(raw, schema)
            restored = rp.restore_envelopes(raw, schema)
            with patch.object(core, 'READOUT_PROFILE', 'blocked-weight-v1'):
                v = core.vector(qs[0], restored['q0']['answer'])
            self.assertEqual(len(v), n)
            self.assertEqual(v.index(max(v)), n - 1)
            if n > 16:
                expected = dict(zip((o['tid'] for o in qs[0]['opts']), values))
                self.assertEqual(rp.restore_blocks(qs[0], answer), expected)
                for invalid in (dict(list(answer.items())[1:]), {**answer, 'b999': [1]},
                                {**answer, 'b0': answer['b0'][:-1]}):
                    with self.assertRaises(ValueError): rp.restore_blocks(qs[0], invalid)
                    with self.assertRaises(ValueError): rp.restore_envelopes({'q0': invalid}, schema)
            validate({'q0': None}, schema)
            self.assertTrue(rp.restore_envelopes({'q0': None}, schema)['q0']['refused'])
            self.assertEqual(payload['reasoning'], base['reasoning'])
            self.assertEqual(payload['max_output_tokens'], base['max_output_tokens'])
            self.assertEqual(payload['input'][:-1], base['input'][:-1])


class ProbabilityProfile(unittest.TestCase):
    def test_complete_named_mapping_and_policy_boundaries(self):
        for n in (1, 16, 17, 77, 150, 255):
            for state in ('short', 'x' * 8500):
                state, qs = core.parse_systemone({'model': 'gpt-6-luna', 'state': state,
                    'questions': {'choice': {'type': 'choice', 'criteria': {f'original_{i}': f'meaning {i}' for i in range(n)}},
                                  'predicate': {'type': 'noul', 'instructions': 'Is the stated condition true?'},
                                  'score': {'type': 'score', 'criteria': ['low', 'high']}}})
                with patch.object(core, 'READOUT_PROFILE', 'baseline'):
                    base = core.build_upstream(state, qs)
                saved = copy.deepcopy(base)
                changed = rp.apply(base, qs, 'keyed-probability-v2')
                for field in ('model', 'reasoning', 'max_output_tokens', 'stream', 'store'):
                    self.assertEqual(changed[field], base[field])
                self.assertEqual(changed['input'][:-1], base['input'][:-1])
                original = json.loads(base['input'][-1]['content'][0]['text'].split('\n', 1)[1])
                updated = json.loads(changed['input'][-1]['content'][0]['text'].split('\n', 1)[1])
                self.assertEqual([(o['key'], o.get('description')) for o in original[0]['outcomes']],
                                 [(o['key'], o.get('description')) for o in updated[0]['outcomes']])
                values = [0.] * n
                values[-1] = 1.
                answer = values if n <= 16 else {f'o{i}': value for i, value in enumerate(values)}
                report = {'q0': answer, 'q1': .75, 'q2': [.2, .8]}
                validate(report, changed['text']['format']['schema'])
                restored = rp.restore_envelopes(report, changed['text']['format']['schema'])
                rp.validate_probabilities(restored, 'keyed-probability-v2')
                with patch.object(core, 'READOUT_PROFILE', 'keyed-probability-v2'):
                    vector = core.vector(qs[0], restored['q0']['answer'])
                self.assertEqual(vector, values)
                self.assertEqual(base, saved)
                self.assertIs(rp.apply(base, qs, 'baseline'), base)
                self.assertNotIn('1000', changed['instructions'])

    def test_reject_missing_tail_out_of_domain_and_zero_mass(self):
        state, qs = core.parse_systemone({'model': 'gpt-6-luna', 'state': 'evidence',
            'questions': {'q': {'type': 'choice', 'criteria': {f'label{i}': str(i) for i in range(17)}}}})
        with patch.object(core, 'READOUT_PROFILE', 'baseline'):
            base = core.build_upstream(state, qs)
        schema = rp.apply(base, qs, 'keyed-probability-v2')['text']['format']['schema']
        valid = {f'o{i}': 1 / 17 for i in range(17)}
        for invalid in (dict(list(valid.items())[1:]), {**valid, 'extra': 0}, {**valid, 'o0': 1.1}, {**valid, 'o0': True}):
            with self.assertRaises((ValueError, ValidationError)):
                rp.restore_envelopes({'q0': invalid}, schema)
        with patch.object(core, 'READOUT_PROFILE', 'keyed-probability-v2'), self.assertRaises(ValueError):
            core.vector(qs[0], {key: 0 for key in valid})
        self.assertTrue(rp.restore_envelopes({'q0': None}, schema)['q0']['refused'])


if __name__=='__main__':unittest.main()
