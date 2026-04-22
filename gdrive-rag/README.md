# gdrive-rag

A Go service that indexes a whitelist of Google Drive folders into an embedded vector database
(chromem-go), extracts text via pure-Go PDF parsing with Gemini Flash fallback for scanned
documents, generates embeddings via Gemini, and exposes an HTTP + MCP surface for querying.
Runs as a standalone container on rpi5 alongside the other iot-fetcher services.
