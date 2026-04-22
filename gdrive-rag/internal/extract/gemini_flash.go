package extract

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"google.golang.org/genai"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/budget"
)

// defaultFlashModel is the Gemini model used when FlashConfig.Model is unset.
const defaultFlashModel = "gemini-2.5-flash-lite"

// defaultFlashPrompt is the extraction instruction sent alongside every Flash
// call. Chosen to bias the model towards verbatim text reproduction rather
// than summarisation.
const defaultFlashPrompt = "Extract all text verbatim. Preserve structure and reading order."

// generateFn mirrors client.Models.GenerateContent so tests can inject a fake
// without talking to the Gemini API.
type generateFn func(ctx context.Context, model string, contents []*genai.Content, config *genai.GenerateContentConfig) (*genai.GenerateContentResponse, error)

// FlashClient wraps the Gemini Files API + generate_content for verbatim text
// extraction from PDFs and images. Safe for concurrent use.
type FlashClient struct {
	client *genai.Client

	model      string
	prompt     string
	rpmLimiter *budget.RateLimiter
	daily      *budget.DailyCounter

	// generate is usually client.Models.GenerateContent. Swapped in tests.
	generate generateFn

	// pollInterval controls how often we poll Files.Get while waiting for the
	// uploaded file to leave the PROCESSING state. Exposed for tests.
	pollInterval time.Duration
	pollTimeout  time.Duration
}

// FlashConfig parameterises a FlashClient.
type FlashConfig struct {
	// APIKey is the Gemini API key. Required unless a custom Client is wired
	// elsewhere. When empty, the genai SDK falls back to GEMINI_API_KEY /
	// GOOGLE_API_KEY env vars.
	APIKey string

	// Model is the Gemini model to call. Defaults to "gemini-2.5-flash-lite".
	Model string

	// RPMLimiter gates every call to GenerateContent. Required if real API
	// calls are expected.
	RPMLimiter *budget.RateLimiter

	// DailyCounter is checked before each call and incremented on success.
	// nil disables daily accounting.
	DailyCounter *budget.DailyCounter

	// Prompt is the instruction sent with every extraction call. Defaults to
	// the verbatim-text prompt if empty.
	Prompt string
}

// NewFlashClient builds a FlashClient using the provided API key.
func NewFlashClient(ctx context.Context, cfg FlashConfig) (*FlashClient, error) {
	client, err := genai.NewClient(ctx, &genai.ClientConfig{
		APIKey:  cfg.APIKey,
		Backend: genai.BackendGeminiAPI,
	})
	if err != nil {
		return nil, fmt.Errorf("extract: new flash client: %w", err)
	}

	model := cfg.Model
	if model == "" {
		model = defaultFlashModel
	}
	prompt := cfg.Prompt
	if prompt == "" {
		prompt = defaultFlashPrompt
	}

	fc := &FlashClient{
		client:       client,
		model:        model,
		prompt:       prompt,
		rpmLimiter:   cfg.RPMLimiter,
		daily:        cfg.DailyCounter,
		pollInterval: 1 * time.Second,
		pollTimeout:  2 * time.Minute,
	}
	fc.generate = func(ctx context.Context, model string, contents []*genai.Content, config *genai.GenerateContentConfig) (*genai.GenerateContentResponse, error) {
		return client.Models.GenerateContent(ctx, model, contents, config)
	}
	return fc, nil
}

// Close is a placeholder for symmetry with other SDK clients. The current
// genai Go SDK doesn't require an explicit shutdown.
func (fc *FlashClient) Close() error { return nil }

// ExtractBytes uploads the bytes to Gemini Files API, runs generate_content,
// and returns the extracted text. mimeHint must be set to the file's MIME
// type (e.g. "application/pdf", "image/png"). Safe for concurrent calls.
//
// Budget coordination:
//   - If the daily counter is maxed before the call, returns
//     budget.ErrDailyBudgetExhausted without touching the RPM limiter or the
//     Gemini API.
//   - On success, increments the daily counter by 1.
//   - On failure, the counter is not charged — the caller can retry.
func (fc *FlashClient) ExtractBytes(ctx context.Context, fileHint, mimeHint string, body []byte) (string, error) {
	if fc == nil {
		return "", errors.New("extract: nil flash client")
	}
	if len(body) == 0 {
		return "", fmt.Errorf("extract: flash: empty body for %s", fileHint)
	}

	// Daily cap first — cheapest check.
	if fc.daily != nil {
		if err := fc.daily.Check(1); err != nil {
			return "", err
		}
	}

	// RPM gate.
	if fc.rpmLimiter != nil {
		if err := fc.rpmLimiter.Wait(ctx, 1); err != nil {
			return "", fmt.Errorf("extract: flash rpm wait: %w", err)
		}
	}

	// Upload the bytes to the Files API.
	uploaded, err := fc.client.Files.Upload(ctx, bytes.NewReader(body), &genai.UploadFileConfig{
		MIMEType:    mimeHint,
		DisplayName: fileHint,
	})
	if err != nil {
		return "", fmt.Errorf("extract: flash upload %s: %w", fileHint, err)
	}
	fileName := uploaded.Name
	fileURI := uploaded.URI

	// Ensure we always attempt to delete the uploaded file so we don't leak
	// storage, even on failure.
	defer func() {
		if fileName == "" {
			return
		}
		// Use a fresh context with a short timeout — the parent may already
		// be cancelled, but we still want the cleanup to go through.
		delCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if _, delErr := fc.client.Files.Delete(delCtx, fileName, nil); delErr != nil {
			slog.Warn("extract: flash file delete failed",
				"file", fileName, "hint", fileHint, "err", delErr)
		}
	}()

	// Wait for the file to become ACTIVE if it's still PROCESSING.
	active, err := fc.waitForFileActive(ctx, uploaded)
	if err != nil {
		return "", fmt.Errorf("extract: flash wait-for-active %s: %w", fileHint, err)
	}
	if active.URI != "" {
		fileURI = active.URI
	}

	// Build the prompt + file reference and call generate_content.
	parts := []*genai.Part{
		genai.NewPartFromText(fc.prompt),
		genai.NewPartFromURI(fileURI, mimeHint),
	}
	contents := []*genai.Content{genai.NewContentFromParts(parts, genai.RoleUser)}

	resp, err := fc.generate(ctx, fc.model, contents, nil)
	if err != nil {
		return "", fmt.Errorf("extract: flash generate %s: %w", fileHint, err)
	}
	text := resp.Text()

	// Success — charge the daily counter.
	if fc.daily != nil {
		fc.daily.Add(1)
	}
	return text, nil
}

// waitForFileActive polls Files.Get until the file is ACTIVE, FAILED, or the
// configured timeout elapses. An already-ACTIVE file short-circuits the poll.
func (fc *FlashClient) waitForFileActive(ctx context.Context, f *genai.File) (*genai.File, error) {
	if f == nil {
		return nil, errors.New("nil file")
	}
	if f.State == genai.FileStateActive || f.State == "" {
		return f, nil
	}
	if f.State == genai.FileStateFailed {
		return nil, fmt.Errorf("file upload failed: %+v", f.Error)
	}
	if f.State == genai.FileStateUnspecified {
		return nil, fmt.Errorf("file %s returned STATE_UNSPECIFIED", f.Name)
	}

	interval := fc.pollInterval
	if interval <= 0 {
		interval = time.Second
	}
	timeout := fc.pollTimeout
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	deadline := time.Now().Add(timeout)

	for {
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("timed out waiting for file %s (state=%s)", f.Name, f.State)
		}

		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}

		got, err := fc.client.Files.Get(ctx, f.Name, nil)
		if err != nil {
			return nil, fmt.Errorf("files.get: %w", err)
		}
		switch got.State {
		case genai.FileStateActive, "":
			return got, nil
		case genai.FileStateFailed:
			return nil, fmt.Errorf("file upload failed: %+v", got.Error)
		case genai.FileStateUnspecified:
			return nil, fmt.Errorf("file %s returned STATE_UNSPECIFIED", got.Name)
		}
	}
}
