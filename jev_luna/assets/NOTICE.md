# Third-party adapters

The complete upstream MIT copyright and permission notices are included in `THIRD_PARTY_NOTICES.md` in the source distribution and the wheel's `dist-info/licenses` directory. Repository links below identify the exact origins; they do not replace those notices.

`jev-upstream.patch` applies to the Boris Lemeec Jev client at the commit pinned in lock.json. The client patches change local routing/authentication and forward the original deadline. The shared jev-hook.patch fixes Claude envelope placement and preserves extra tool-input arguments. The original `find`, `ask`, `hook`, candidate policy, thresholds and evidence windows remain unchanged. `scripts/prepare_adapters.py` verifies exact locked source and patch digests.

`codex-selector.patch` is empty. The MIT-licensed James Cressler Jev Codex Token Saver version `0.3.2+codex.20260920154434` retains its original source and bundle; `jev-local.mjs` redirects its decision requests to the authenticated local service, retaining payload bytes and the original AbortSignal without changing candidate selection or evidence windows.

Optional built binaries/bundles are generated from users' installed sources and are excluded from Git. Original source licenses remain in the preparation directories.

Upstreams: https://github.com/BorisLeMeec/jev and https://github.com/jcressler/jev-codex-token-saver.

## Activation and recovery

The optional native adapters keep the original application's activation rules.
Use Jev `find` for a feature whose symbol name is unknown, or `ask` for a yes/no
property across a bounded set of files; supply a concrete goal and authorized
paths. Use ordinary exact search for a known symbol, and read the real content
before editing. Jev returns typed numbers and locations; it does not generate
explanatory text. CLI errors remain visible and are not permission to scan more.

The Read hook remains the original `PreToolUse` matcher `Read`; it is not a
per-event model call. A readable transcript goal, an unwindowed large file and
the original safety gates must all pass. Failure leaves the Read unchanged. The
original host hook timeout is 20 seconds; the native HTTP client retains its
120-second per-attempt budget. `JEV_HOOK_DISABLE=1` disables narrowing; an explicit
Read offset or limit bypasses it. Host termination has not been proven to cancel
a subscription request and must not be described as such.

The unchanged Codex selector exposes workspace search, large text selection and
selected-range reading. Supply the authorized absolute workspace, a concrete
question and, when useful, distinct evidence requirements; provide a relative
file for large-text reads and a prior session ID for selected reads. Follow its
original one-pass plus one targeted recovery instructions. Small candidate
packets bypass Jev; faults expose deterministic local fallback. Neither result
proves model connectivity. Do not retry Jev after reported fallback. The model
HTTP path must report `mode=jev` and correlate with candidate-service trace.

Start the local HTTP service separately. The host starts only its stdio adapter;
use an absolute interpreter/binary path from the verified candidate installation.
The Node preload routes only declared Decision endpoints, retains caller aborts,
reopens the protected local key for each request, and refuses conflicting repeated
loads. Restart the adapter process to change its endpoint or app-state directory.
These adapters do not add another model or provide a text-generation backend.
