package main

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds all env-driven runtime settings for gdrive-rag. Every field
// maps 1:1 to an RAG_* or GOOGLE_* env var documented in .env.template.
type Config struct {
	// Credentials.
	ServiceAccountJSON string
	GeminiAPIKey       string

	// Indexing.
	WhitelistedFolders []string
	EmbedModel         string
	ExtractModel       string

	// Tuning.
	SyncInterval   time.Duration
	ChunkTokens    int
	ChunkOverlap   int
	EmbedBatchSize int
	ListenAddr     string
	DataDir        string
	LogLevel       string

	// Budget caps.
	EmbedTPMCap    int64
	EmbedRPMCap    int64 // reserved; currently unused
	FlashRPMCap    int
	FlashDailyCap  int64

	// File-size cutoffs.
	MaxFileSizeMB      int
	MaxPDFPages        int
	PDFMaxPagesPerCall int
	PDFMinCharsPerPage int
	SkipImages         bool
}

// LoadConfig reads Config from the process environment and fails fast if any
// required value is missing or malformed.
func LoadConfig() (*Config, error) {
	c := &Config{
		ServiceAccountJSON: strings.TrimSpace(os.Getenv("GOOGLE_SERVICE_ACCOUNT")),
		GeminiAPIKey:       strings.TrimSpace(os.Getenv("GEMINI_API_KEY")),
		WhitelistedFolders: splitCSV(os.Getenv("RAG_ROOT_FOLDER_IDS")),

		EmbedModel:   getenv("RAG_EMBED_MODEL", "gemini-embedding-001"),
		ExtractModel: getenv("RAG_EXTRACT_MODEL", "gemini-2.5-flash-lite"),
		ListenAddr:   getenv("RAG_LISTEN_ADDR", ":8090"),
		DataDir:      getenv("RAG_DATA_DIR", "/data"),
		LogLevel:     getenv("RAG_LOG_LEVEL", "info"),
	}

	var err error
	if c.SyncInterval, err = getDuration("RAG_SYNC_INTERVAL", 10*time.Minute); err != nil {
		return nil, err
	}
	if c.ChunkTokens, err = getInt("RAG_CHUNK_TOKENS", 800); err != nil {
		return nil, err
	}
	if c.ChunkOverlap, err = getInt("RAG_CHUNK_OVERLAP", 100); err != nil {
		return nil, err
	}
	if c.EmbedBatchSize, err = getInt("RAG_EMBED_BATCH_SIZE", 25); err != nil {
		return nil, err
	}
	if c.EmbedTPMCap, err = getInt64("RAG_EMBED_TPM_CAP", 200_000); err != nil {
		return nil, err
	}
	if c.EmbedRPMCap, err = getInt64("RAG_EMBED_RPM_CAP", 10); err != nil {
		return nil, err
	}
	if c.FlashRPMCap, err = getInt("RAG_FLASH_RPM_CAP", 10); err != nil {
		return nil, err
	}
	if c.FlashDailyCap, err = getInt64("RAG_FLASH_DAILY_REQUEST_CAP", 800); err != nil {
		return nil, err
	}
	if c.MaxFileSizeMB, err = getInt("RAG_MAX_FILE_SIZE_MB", 50); err != nil {
		return nil, err
	}
	if c.MaxPDFPages, err = getInt("RAG_MAX_PDF_PAGES", 500); err != nil {
		return nil, err
	}
	if c.PDFMaxPagesPerCall, err = getInt("RAG_PDF_MAX_PAGES_PER_CALL", 20); err != nil {
		return nil, err
	}
	if c.PDFMinCharsPerPage, err = getInt("RAG_PDF_TEXT_LAYER_MIN_CHARS_PER_PAGE", 100); err != nil {
		return nil, err
	}
	if c.SkipImages, err = getBool("RAG_SKIP_IMAGE_EXTRACTION", false); err != nil {
		return nil, err
	}

	if err := c.validate(); err != nil {
		return nil, err
	}
	return c, nil
}

func (c *Config) validate() error {
	missing := []string{}
	if c.ServiceAccountJSON == "" {
		missing = append(missing, "GOOGLE_SERVICE_ACCOUNT")
	}
	if c.GeminiAPIKey == "" {
		missing = append(missing, "GEMINI_API_KEY")
	}
	if len(c.WhitelistedFolders) == 0 {
		missing = append(missing, "RAG_ROOT_FOLDER_IDS")
	}
	if len(missing) > 0 {
		return fmt.Errorf("missing required env vars: %s", strings.Join(missing, ", "))
	}
	if c.SyncInterval <= 0 {
		return errors.New("RAG_SYNC_INTERVAL must be > 0")
	}
	if c.ChunkTokens <= 0 {
		return errors.New("RAG_CHUNK_TOKENS must be > 0")
	}
	if c.ChunkOverlap < 0 {
		return errors.New("RAG_CHUNK_OVERLAP must be >= 0")
	}
	if c.MaxFileSizeMB <= 0 {
		return errors.New("RAG_MAX_FILE_SIZE_MB must be > 0")
	}
	if c.EmbedTPMCap <= 0 {
		return errors.New("RAG_EMBED_TPM_CAP must be > 0")
	}
	if c.FlashRPMCap <= 0 {
		return errors.New("RAG_FLASH_RPM_CAP must be > 0")
	}
	if c.FlashDailyCap <= 0 {
		return errors.New("RAG_FLASH_DAILY_REQUEST_CAP must be > 0")
	}
	return nil
}

// --- env helpers ------------------------------------------------------------

func getenv(k, def string) string {
	if v, ok := os.LookupEnv(k); ok {
		v = strings.TrimSpace(v)
		if v != "" {
			return v
		}
	}
	return def
}

func getInt(k string, def int) (int, error) {
	v, ok := os.LookupEnv(k)
	if !ok {
		return def, nil
	}
	v = strings.TrimSpace(v)
	if v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", k, err)
	}
	return n, nil
}

func getInt64(k string, def int64) (int64, error) {
	v, ok := os.LookupEnv(k)
	if !ok {
		return def, nil
	}
	v = strings.TrimSpace(v)
	if v == "" {
		return def, nil
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", k, err)
	}
	return n, nil
}

func getDuration(k string, def time.Duration) (time.Duration, error) {
	v, ok := os.LookupEnv(k)
	if !ok {
		return def, nil
	}
	v = strings.TrimSpace(v)
	if v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", k, err)
	}
	return d, nil
}

func getBool(k string, def bool) (bool, error) {
	v, ok := os.LookupEnv(k)
	if !ok {
		return def, nil
	}
	v = strings.TrimSpace(v)
	if v == "" {
		return def, nil
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return false, fmt.Errorf("%s: %w", k, err)
	}
	return b, nil
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}
