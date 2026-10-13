"""Native credential generation, refresh, RPC fault, trace completeness and typed math checks; offline."""
import hashlib
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jev_luna import core, native_rpc as rpc, subscription_auth as auth
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext, bind
from jev_luna.receipts import RequestTrace, flush_traces, require_complete_trace


def status(token="t0"):
    return {"requiresOpenaiAuth": True, "authMethod": "chatgpt", "authToken": token}


def account(owner="A"):
    return {"requiresOpenaiAuth": True, "account": {"type": "chatgpt"},
            "workspaceRouting": {"chatgptAccountId": owner,
                                 "backendOrigin": "https://chatgpt.com",
                                 "accountRoutingOverride": "NO_CONSTRAINT"}}


def fake_session(handler):
    session = rpc.Session.__new__(rpc.Session)
    session.account_epoch = 0
    session.broken = threading.Event()
    session.rpc = handler
    return session


class CredentialTests(unittest.TestCase):
    def test_generation_preserves_private_existing_format(self):
        value = rpc.validate_pair(status(), account())
        digest = hashlib.sha256(b"A\0t0").hexdigest()
        self.assertEqual(value.generation[:2], ("native", digest))
        self.assertEqual(value.generation[2], "NO_CONSTRAINT")
        self.assertNotIn("t0", repr(value))

    def test_null_mode_is_not_proof_that_login_is_absent(self):
        session = fake_session(lambda *a: {"requiresOpenaiAuth": True, "authMethod": None})
        with self.assertRaises(rpc.NativeRpcError) as caught:
            session.credential(time.monotonic()+1)
        self.assertEqual(caught.exception.code, "native_login_unavailable")
        self.assertEqual(caught.exception.status, 422)

    def test_owner_switch_cannot_become_success_by_stabilizing_on_B(self):
        replies = iter([status(), status(), account("A"), status("tB"), account("B"),
                        status("tB"), account("B"), status("tB"), account("B")])
        session = fake_session(lambda *a: next(replies))
        with self.assertRaises(rpc.NativeRpcError) as caught:
            session.credential(time.monotonic()+1)
        self.assertEqual(caught.exception.status, 409)

    def test_compare_and_refresh_share_session_and_expected_owner(self):
        calls, token = [], ["t0"]
        def peer(method, params, deadline):
            calls.append((method, dict(params)))
            if method == "account/read":
                return account()
            if params.get("refreshToken"):
                token[0] = "t1"
            return status(token[0])
        session = fake_session(peer)
        expected = rpc.validate_pair(status(), account()).generation
        value = session.credential(time.monotonic()+1, force_refresh=True,
                                   expected_generation=expected, expected_account="A")
        self.assertEqual(value.access_token, "t1")
        self.assertEqual(sum(p.get("refreshToken", False) for _, p in calls), 1)
        self.assertTrue(all(m in rpc.ALLOWED_RPC for m, _ in calls))

    def test_wrong_owner_never_reaches_explicit_refresh(self):
        calls = []
        def peer(method, params, deadline):
            calls.append((method, params))
            return account("B") if method == "account/read" else status("tB")
        session = fake_session(peer)
        with self.assertRaises(rpc.NativeRpcError) as caught:
            session.credential(time.monotonic()+1, force_refresh=True, expected_account="A")
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(any(p.get("refreshToken") for _, p in calls))

    def test_cancellation_detaches_waiter_not_manager(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        results = []
        def lookup(*a, **k):
            entered.set()
            release.wait(2)
            finished.set()
            return "t0", "A", ("native", "generation")
        ctx = RequestContext(time.monotonic()+5)
        def waiter():
            try:
                auth._credentials(5, context=ctx)
            except BaseException as e:
                results.append(e)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(auth, "auth_file", return_value=Path(directory)/"auth.json"), \
             patch.object(auth, "_native_login", side_effect=lookup):
            worker = threading.Thread(target=waiter)
            try:
                worker.start()
                self.assertTrue(entered.wait(1))
                ctx.cancel()
                worker.join(.3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(results[0].status, 499)
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                worker.join(2)
                self.assertTrue(finished.wait(1))
                # Let the shared worker publish its terminal event before restoring mocks.
                end = time.monotonic()+1
                while time.monotonic()<end:
                    with auth._lookup_lock:
                        flights = list(auth._lookups.values())
                    if not flights:
                        break
                    time.sleep(.01)

    def test_no_completed_credential_reuse(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(auth, "auth_file", return_value=Path(directory)/"auth.json"), \
             patch.object(auth, "_native_login", return_value=("t", "A", ("native", "g"))) as lookup:
            auth._credentials(1)
            auth._credentials(1)
            self.assertEqual(lookup.call_count, 2)


class RefreshErrorTests(unittest.TestCase):
    def test_core_does_not_flatten_native_failure_to_401(self):
        state, questions = core.parse_systemone({"model": "jev-latest", "state": "x",
            "questions": {"q": {"type": "noul", "instructions": "True?"}}})
        for code, expected in (("native_account_changed", 409), ("native_regional_route", 422),
                               ("native_auth_deadline", 504)):
            with self.subTest(code=code):
                ctx = RequestContext(time.monotonic()+2)
                rejected = Reject(401, "token_expired", "expired")
                rejected.auth_recoverable = True
                with patch.object(core, "TRANSPORT", "subscription"), \
                     patch.object(core, "prepare_auth", return_value=SimpleNamespace(auth_generation=("native", "g"))), \
                     patch.object(core, "upstream_call", side_effect=rejected), \
                     patch.object(auth, "refresh", side_effect=auth.NativeAuthStateError("safe", code=code, status=expected)), \
                     bind(ctx):
                    with self.assertRaises(Reject) as caught:
                        core._infer_batch(state, questions, ctx.deadline)
                self.assertEqual((caught.exception.code, caught.exception.status), (code, expected))


class RpcFaultTests(unittest.TestCase):
    def test_broken_channel_cannot_consume_prequeued_success(self):
        session = rpc.Session.__new__(rpc.Session)
        session.closed = False
        session.sequence = 0
        session.account_epoch = 0
        session.broken = threading.Event()
        session.broken.set()
        session.inbox = queue.Queue()
        session.inbox.put(json.dumps({"id": 1, "result": status()}).encode())
        session._write = Mock()
        session._terminate = Mock()
        with self.assertRaises(rpc.NativeRpcError) as caught:
            session.rpc("getAuthStatus", {}, time.monotonic()+1)
        self.assertEqual(caught.exception.status, 502)

    def test_nonreading_peer_cannot_block_control_writer(self):
        session = rpc.Session.__new__(rpc.Session)
        session.deadline = time.monotonic()+.03
        session.proc = Mock()
        session.proc.poll.return_value = None
        session.proc.stdin.fileno.return_value = 123
        with patch.object(rpc.os, "write", side_effect=BlockingIOError):
            with self.assertRaises(rpc.NativeRpcError) as caught:
                session._write({"method": "initialized"})
        self.assertEqual(caught.exception.status, 504)


class TraceTests(unittest.TestCase):
    def test_complete_gate_rejects_overflow_and_partial_files(self):
        good = {"phase": "finished", "diagnostics_complete": True, "dropped_events": 0,
                "events": [{"event": "admitted"}, {"event": "finished"}]}
        self.assertIs(require_complete_trace(good), good)
        for change in ({"dropped_events": 1}, {"dropped_events": False},
                       {"diagnostics_complete": False}, {"diagnostics_complete": 1},
                       {"phase": "admitted"}, {"events": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_complete_trace({**good, **change})

    def test_drained_queue_is_not_a_finished_request(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = RequestTrace(directory, "in-progress", {})
            self.assertTrue(flush_traces(2))
            self.assertFalse(trace.flush(.1))
            trace("finished", {})
            self.assertTrue(trace.flush(2))


class TypedMathTests(unittest.TestCase):
    def test_singleton_keeps_refusal_and_typed_math(self):
        _, questions = core.parse_systemone({"model": "jev-latest", "state": "x",
            "questions": {"one": {"type": "choice", "criteria": {"only": "the sole outcome"}},
                          "score": {"type": "score", "criteria": ["only level"]}}})
        for q in questions:
            self.assertEqual(core._schema(q)["items"]["enum"], [1])
            self.assertEqual(core.vector(q, [1]), [1])
            self.assertEqual(core.typed(q, [1])["conf"], 1)
            with self.assertRaises(ValueError):
                core.vector(q, [0])
        self.assertEqual(core.typed(questions[1], [1])["score"], 0)

    def test_score_fields_and_wire_labels_preserved(self):
        body={"model":"gpt-6-luna", "input":"x", "questions":[{"type":"score", "instructions":"Compare exact labels", "levels":[
            {"label":"a: b", "description":"c"}, {"label":"a", "description":"b: c"}]}]}
        _, questions, meta=core.parse_decisions(body)
        self.assertNotEqual(questions[0]["opts"][0]["desc"], questions[0]["opts"][1]["desc"])
        answer=core.typed(questions[0], [.25,.75])
        result=core.render_decisions(meta,[answer])[0]
        self.assertEqual(result["score"], .75)
        self.assertEqual([p["label"] for p in result["probabilities"]], ["a: b", "a"])
        self.assertEqual([p["value"] for p in result["probabilities"]], [0,1])


if __name__ == "__main__":
    unittest.main()
