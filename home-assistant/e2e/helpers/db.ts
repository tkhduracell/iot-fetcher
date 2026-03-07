import Database from "better-sqlite3";
import path from "path";
import fs from "fs";

const TEST_DB_DIR = path.join(__dirname, "..", ".test-data");

/**
 * Create a fresh test database with the app schema.
 * Returns the database instance and the file path (for SQLITE_PATH env var).
 */
export function createTestDb(testName: string): {
  db: Database.Database;
  dbPath: string;
} {
  if (!fs.existsSync(TEST_DB_DIR)) {
    fs.mkdirSync(TEST_DB_DIR, { recursive: true });
  }

  const dbPath = path.join(TEST_DB_DIR, `${testName}-${Date.now()}.db`);
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      user_email TEXT NOT NULL,
      persona TEXT NOT NULL,
      title TEXT DEFAULT '',
      model TEXT DEFAULT 'gemini-3-flash-preview',
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      content TEXT NOT NULL DEFAULT '',
      tool_calls TEXT DEFAULT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_email);
  `);

  return { db, dbPath };
}

/**
 * Seed a chat session into the test database.
 */
export function seedSession(
  db: Database.Database,
  opts: {
    id: string;
    userEmail: string;
    persona: string;
    title?: string;
    model?: string;
  }
): void {
  db.prepare(
    `INSERT INTO sessions (id, user_email, persona, title, model) VALUES (?, ?, ?, ?, ?)`
  ).run(
    opts.id,
    opts.userEmail,
    opts.persona,
    opts.title ?? "",
    opts.model ?? "gemini-3-flash-preview"
  );
}

/**
 * Seed a message into the test database.
 */
export function seedMessage(
  db: Database.Database,
  opts: {
    sessionId: string;
    role: string;
    content: string;
    toolCalls?: unknown[];
  }
): void {
  db.prepare(
    `INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, ?, ?, ?)`
  ).run(
    opts.sessionId,
    opts.role,
    opts.content,
    opts.toolCalls ? JSON.stringify(opts.toolCalls) : null
  );
}

/**
 * Clean up a test database file.
 */
export function cleanupTestDb(dbPath: string): void {
  for (const suffix of ["", "-wal", "-shm"]) {
    const file = dbPath + suffix;
    if (fs.existsSync(file)) {
      fs.unlinkSync(file);
    }
  }
}

/**
 * Remove the entire test data directory.
 */
export function cleanupAllTestDbs(): void {
  if (fs.existsSync(TEST_DB_DIR)) {
    fs.rmSync(TEST_DB_DIR, { recursive: true, force: true });
  }
}
