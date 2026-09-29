# Loopback observer connection boundary

Collector `loopback-http-observer-v3-pinned-origin-tls12` connects only to the
operator-declared loopback fixture. The request URL is validated against that
origin, but never supplies the host/port to the actual HTTPConnection. The
forwarded Host header is generated from the declared origin, not copied from
the client. Redirects are relayed, not followed by the observer.

Literal loopback addresses retain their address identity. `localhost` and
`localhost.localdomain` explicitly select `127.0.0.1`, without a DNS lookup;
an IPv6-only fixture must declare `http(s)://[::1]:port`. This avoids relying on
mutable name resolution and makes the selected connection deterministic.

HTTPS uses an operator-supplied fixture certificate/key for the observer's
local CONNECT endpoint. Both TLS contexts require TLS 1.2 or newer. Upstream
certificate-chain verification is required against the supplied fixture
certificate trust file. Hostname checking is disabled only for these explicit
local fixtures, which may use CN-only certificates; this is not a general
internet TLS proxy. Missing fixture trust fails closed. The certificate/private
key contents are never stored in the observation artifact.

CONNECT applies a timeout before the TLS handshake and closes upstream,
decrypted reader and TLS client on both success and exceptions. An observer
created for configuration checks can be closed without starting its server.

These changes address connection routing and TLS boundaries, not the full
evidence-trust problem. Transport metadata still does not prove impact. Clients
that bypass the proxy are not observed unless the OS backend forces capture.
Predicate scope, response truncation/completeness, causal controls and target
write access to evidence remain subject to R07's separate acceptance criteria.
Do not claim those criteria are met from these connection tests alone.

Tests: `tests/test_http_observer_boundaries.py` exercises pinned aliases,
origin rejection, Host-header replacement, required TLS trust/minimum version
and failure cleanup; `tests/test_hardening_controls.py` retains the real HTTPS
fixture round trip and verifies that sensitive response contents are absent
from the observer snapshot.
