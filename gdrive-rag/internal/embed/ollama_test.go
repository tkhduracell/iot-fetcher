package embed

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOllamaClient_BatchesAndPrefixesQueries(t *testing.T) {
	var inputs [][]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/embed" {
			t.Errorf("path = %s", r.URL.Path)
		}
		var req ollamaEmbedRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		inputs = append(inputs, req.Input)
		out := ollamaEmbedResponse{PromptEvalCount: int64(len(req.Input))}
		for range req.Input {
			out.Embeddings = append(out.Embeddings, []float32{1, 0})
		}
		_ = json.NewEncoder(w).Encode(out)
	}))
	defer srv.Close()

	var tokens int64
	c, err := NewOllamaClient(OllamaConfig{
		URL: srv.URL + "/", Model: "m", BatchSize: 2, QueryPrefix: "Q: ",
		RecordTokens: func(n int64) { tokens += n },
	})
	if err != nil {
		t.Fatal(err)
	}
	vecs, err := c.EmbedBatch(context.Background(), []string{"a", "b", "c"})
	if err != nil || len(vecs) != 3 {
		t.Fatalf("EmbedBatch = %d vecs, err %v", len(vecs), err)
	}
	if len(inputs) != 2 || len(inputs[0]) != 2 || inputs[0][0] != "a" {
		t.Fatalf("batches = %v", inputs)
	}
	if _, err := c.EmbedQuery(context.Background(), "energideklaration"); err != nil {
		t.Fatal(err)
	}
	if got := inputs[2][0]; got != "Q: energideklaration" {
		t.Fatalf("query input = %q", got)
	}
	if tokens != 4 {
		t.Fatalf("tokens = %d; want 4", tokens)
	}
}

func TestOllamaClient_ServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"error":"model \"m\" not found"}`))
	}))
	defer srv.Close()
	c, _ := NewOllamaClient(OllamaConfig{URL: srv.URL, Model: "m"})
	_, err := c.EmbedBatch(context.Background(), []string{"a"})
	if err == nil || !strings.Contains(err.Error(), "not found") {
		t.Fatalf("err = %v", err)
	}
}
