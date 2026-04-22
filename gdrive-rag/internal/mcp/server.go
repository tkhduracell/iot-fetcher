// Package mcp exposes the Model Context Protocol server surface so Claude and
// other MCP clients can query the index.
//
// The server is wired with the Streamable HTTP transport from mark3labs/mcp-go,
// so it can be mounted on any path of an existing http.ServeMux — see
// NewServer.
package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"

	mcplib "github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	"github.com/tkhduracell/iot-fetcher/gdrive-rag/internal/api"
)

// version is advertised to MCP clients as the server version. Kept in sync with
// the semver of the overall gdrive-rag service; not currently baked from git.
const version = "0.1.0"

// NewServer builds an MCP server wrapping the given api.Service and returns an
// http.Handler that speaks the Streamable HTTP transport. Mount it at /mcp (or
// wherever fits) alongside the plain HTTP handlers.
func NewServer(svc *api.Service, logger *slog.Logger) (http.Handler, error) {
	if svc == nil {
		return nil, errors.New("mcp: api.Service is required")
	}
	if logger == nil {
		logger = slog.Default()
	}

	s := server.NewMCPServer(
		"gdrive-rag",
		version,
		server.WithToolCapabilities(false),
		server.WithRecovery(),
	)

	registerSearchDrive(s, svc, logger)
	registerReindex(s, svc, logger)
	registerStatus(s, svc, logger)

	return server.NewStreamableHTTPServer(s), nil
}

func registerSearchDrive(s *server.MCPServer, svc *api.Service, logger *slog.Logger) {
	tool := mcplib.NewTool("search_drive",
		mcplib.WithDescription("Search indexed Google Drive chunks by natural-language query. Returns the top-k semantically similar chunks, each with the original file's Drive link so the caller can open the document for context."),
		mcplib.WithString("query",
			mcplib.Required(),
			mcplib.Description("Natural-language search query."),
		),
		mcplib.WithNumber("top_k",
			mcplib.Description("Maximum number of hits to return. Defaults to 10; clamped to 50."),
			mcplib.Min(1),
			mcplib.Max(float64(api.MaxTopK)),
		),
		mcplib.WithString("folder",
			mcplib.Description("Optional folder path prefix filter (matches Chunk.FolderPath)."),
		),
		mcplib.WithString("mime",
			mcplib.Description("Optional exact MIME-type filter (e.g. application/pdf)."),
		),
	)

	s.AddTool(tool, func(ctx context.Context, req mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
		query, err := req.RequireString("query")
		if err != nil {
			return mcplib.NewToolResultError(err.Error()), nil
		}
		apiReq := api.QueryRequest{
			Query:        query,
			TopK:         req.GetInt("top_k", 0),
			FolderFilter: req.GetString("folder", ""),
			MimeFilter:   req.GetString("mime", ""),
		}
		hits, err := svc.Query(ctx, apiReq)
		if err != nil {
			logger.Warn("mcp: search_drive failed", "err", err)
			return mcplib.NewToolResultErrorFromErr("search failed", err), nil
		}
		body, err := json.Marshal(hits)
		if err != nil {
			return mcplib.NewToolResultErrorFromErr("encode hits", err), nil
		}
		return mcplib.NewToolResultText(string(body)), nil
	})
}

func registerReindex(s *server.MCPServer, svc *api.Service, logger *slog.Logger) {
	tool := mcplib.NewTool("reindex",
		mcplib.WithDescription("Manually kick off a full scan of one folder (or every whitelisted folder when folder is omitted). Diagnostic — bypasses the normal Drive changes.list tick."),
		mcplib.WithString("folder",
			mcplib.Description("Optional Drive folder ID. Must be in the configured whitelist. Omit to reindex all whitelisted folders."),
		),
	)

	s.AddTool(tool, func(ctx context.Context, req mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
		folder := req.GetString("folder", "")
		// Fire-and-forget like the HTTP endpoint.
		go func() {
			bg := context.Background()
			if err := svc.Reindex(bg, api.ReindexRequest{Folder: folder}); err != nil {
				logger.Error("mcp: reindex failed", "folder", folder, "err", err)
				return
			}
			logger.Info("mcp: reindex complete", "folder", folder)
		}()
		msg := fmt.Sprintf(`{"status":"accepted","folder":%q}`, folder)
		return mcplib.NewToolResultText(msg), nil
	})
}

func registerStatus(s *server.MCPServer, svc *api.Service, logger *slog.Logger) {
	tool := mcplib.NewTool("status",
		mcplib.WithDescription("Return the current sync/index status: last_sync, counts, queue depth, daily budget usage, and model selection."),
	)

	s.AddTool(tool, func(ctx context.Context, _ mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
		resp := svc.Status(ctx)
		body, err := json.Marshal(resp)
		if err != nil {
			logger.Error("mcp: status marshal failed", "err", err)
			return mcplib.NewToolResultErrorFromErr("encode status", err), nil
		}
		return mcplib.NewToolResultText(string(body)), nil
	})
}
