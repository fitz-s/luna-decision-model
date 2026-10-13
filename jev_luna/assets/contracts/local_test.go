package typesafe

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestLocalUsesOnlyProtectedServiceCredentialAndOriginalDeadline(t *testing.T) {
	home := t.TempDir()
	key := "jvl_local_" + strings.Repeat("fixture", 8)
	if err := os.WriteFile(filepath.Join(home, "service.key"), []byte(key), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("JEV_LUNA_HOME", home)
	t.Setenv("TYPE_SAFE_AI_KEY", "native-paid-key-that-must-not-be-used")
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer "+key {
			t.Error("local credential was not selected")
		}
		remaining, err := strconv.Atoi(r.Header.Get("X-Decide-D-Timeout-Ms"))
		if err != nil || remaining <= 0 || remaining > 15000 {
			t.Error("original caller deadline was not forwarded")
		}
		if calls == 1 {
			w.WriteHeader(502)
			fmt.Fprint(w, `{"error":{"code":"temporary"}}`)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"model":"gpt-6-luna","answers":{"yes":{"type":"noul","noul":0.9}},"usage":{"input_tokens":3,"output_tokens":1}}`)
	}))
	defer server.Close()
	t.Setenv("JEV_URL", server.URL+"/v1/systemone")
	client, err := New()
	if err != nil {
		t.Fatal(err)
	}
	client.minInterval = 0
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	result, err := client.Ask(ctx, "x", map[string]Question{"yes": {Type: "noul", Instructions: "x"}})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 2 || result.Answers["yes"].Noul != .9 {
		t.Fatal("native retry or response changed")
	}
}

func TestRemoteOrMalformedEndpointCannotReadNativeAPIKeys(t *testing.T) {
	t.Setenv("TYPE_SAFE_AI_KEY", "native-paid-key-that-must-not-be-used")
	for _, url := range []string{"https://api.typesafe.ai/v1/systemone", "http://example.org/v1/systemone", "http://user@127.0.0.1:20141/v1/systemone", "http://127.0.0.1:20141/v1/systemone?other=1", "http://127.0.0.1:20141/v1/other"} {
		t.Setenv("JEV_URL", url)
		if _, err := Key(); err == nil {
			t.Fatalf("unsafe endpoint accepted: %s", url)
		}
	}
}

// A new HTTP attempt receives a fresh native timeout, including after backoff.
type recordingRoundTripper struct {
	calls     int
	deadlines []time.Time
	t         *testing.T
}

func (r *recordingRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	r.calls++
	d, ok := req.Context().Deadline()
	if !ok {
		r.t.Fatal("HTTP timeout missing")
	}
	r.deadlines = append(r.deadlines, d)
	ms, err := strconv.Atoi(req.Header.Get("X-Decide-D-Timeout-Ms"))
	if err != nil || ms < 119900 || ms > 120000 {
		r.t.Fatal("per-attempt timeout changed", ms)
	}
	status := 200
	body := `{"answers":{"yes":{"type":"noul","noul":0.9}}}`
	if r.calls == 1 {
		status = 503
		body = `{"error":{"code":"temporary"}}`
	}
	return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)), Request: req}, nil
}
func TestBackgroundAskPreservesFreshPerAttemptBudget(t *testing.T) {
	t.Setenv("JEV_URL", "http://127.0.0.1:20141/v1/systemone")
	rt := &recordingRoundTripper{t: t}
	client := &Client{key: "fixture", model: DefaultModel, http: &http.Client{Timeout: 120 * time.Second, Transport: rt}}
	if _, err := client.Ask(context.Background(), "x", map[string]Question{"yes": Noul("x")}); err != nil {
		t.Fatal(err)
	}
	if rt.calls != 2 || rt.deadlines[1].Sub(rt.deadlines[0]) < 400*time.Millisecond {
		t.Fatal("HTTP attempts shared an artificial Ask deadline")
	}
}
func TestLocalRedirectNeverForwardsRequestOrCredential(t *testing.T) {
	for _, status := range []int{307, 308} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			reached := 0
			target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached++; fmt.Fprint(w, "unexpected") }))
			defer target.Close()
			origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Location", target.URL+"/v1/systemone")
				w.WriteHeader(status)
			}))
			defer origin.Close()
			home := t.TempDir()
			os.WriteFile(filepath.Join(home, "service.key"), []byte("jvl_local_"+strings.Repeat("fixture", 8)), 0600)
			t.Setenv("JEV_LUNA_HOME", home)
			t.Setenv("JEV_URL", origin.URL+"/v1/systemone")
			client, err := New()
			if err != nil {
				t.Fatal(err)
			}
			client.minInterval = 0
			_, err = client.Ask(context.Background(), "private evidence", map[string]Question{"yes": Noul("x")})
			if err == nil || reached != 0 {
				t.Fatal("redirect forwarded the local request", reached, err)
			}
		})
	}
}
func TestParentDeadlineBoundsSlowResponseBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer server.Close()
	t.Setenv("JEV_URL", server.URL+"/v1/systemone")
	client := &Client{key: "fixture", model: DefaultModel, http: &http.Client{Timeout: 120 * time.Second}}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()
	start := time.Now()
	if _, err := client.Ask(ctx, "x", map[string]Question{"yes": Noul("x")}); err == nil {
		t.Fatal("slow body accepted")
	}
	if time.Since(start) > time.Second {
		t.Fatal("parent cancellation did not close body")
	}
}
