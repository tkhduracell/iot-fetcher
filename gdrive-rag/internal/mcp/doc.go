// Package mcp exposes the Model Context Protocol server surface so Claude and
// other MCP clients can query the index.
package mcp

import (
	// Pin dependencies used by upcoming tasks so go mod tidy retains them
	// in go.mod during scaffolding.
	_ "github.com/mark3labs/mcp-go/mcp"
	_ "github.com/mark3labs/mcp-go/server"
)
