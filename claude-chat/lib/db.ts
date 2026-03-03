import Database from "better-sqlite3";
import path from "path";

const DB_PATH = process.env.SQLITE_PATH ?? path.join(process.cwd(), "data", "chat.db");

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;

  // Ensure data directory exists
  const dir = path.dirname(DB_PATH);
  const fs = require("fs");
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  _db = new Database(DB_PATH);
  _db.pragma("journal_mode = WAL");
  _db.pragma("foreign_keys = ON");

  // Initialize schema
  _db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      user_email TEXT NOT NULL,
      persona TEXT NOT NULL,
      title TEXT DEFAULT '',
      model TEXT DEFAULT 'gemini-2.0-flash',
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

  return _db;
}

// --- Session helpers ---

export type Session = {
  id: string;
  user_email: string;
  persona: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
};

export type DbMessage = {
  id: number;
  session_id: string;
  role: string;
  content: string;
  tool_calls: string | null;
  created_at: string;
};

export function createSession(id: string, userEmail: string, persona: string, model?: string): Session {
  const db = getDb();
  const stmt = db.prepare(
    `INSERT INTO sessions (id, user_email, persona, model) VALUES (?, ?, ?, ?) RETURNING *`
  );
  return stmt.get(id, userEmail, persona, model ?? "gemini-2.0-flash") as Session;
}

export function getSession(id: string): Session | undefined {
  const db = getDb();
  return db.prepare("SELECT * FROM sessions WHERE id = ?").get(id) as Session | undefined;
}

export function listSessions(userEmail: string): Session[] {
  const db = getDb();
  return db
    .prepare("SELECT * FROM sessions WHERE user_email = ? ORDER BY updated_at DESC")
    .all(userEmail) as Session[];
}

export function deleteSession(id: string): void {
  const db = getDb();
  db.prepare("DELETE FROM sessions WHERE id = ?").run(id);
}

export function updateSession(id: string, updates: Partial<Pick<Session, "title" | "model">>): Session | undefined {
  const db = getDb();
  const fields: string[] = ["updated_at = datetime('now')"];
  const values: unknown[] = [];

  if (updates.title !== undefined) {
    fields.push("title = ?");
    values.push(updates.title);
  }
  if (updates.model !== undefined) {
    fields.push("model = ?");
    values.push(updates.model);
  }

  values.push(id);
  db.prepare(`UPDATE sessions SET ${fields.join(", ")} WHERE id = ?`).run(...values);
  return getSession(id);
}

// --- Message helpers ---

export function addMessage(sessionId: string, role: string, content: string, toolCalls?: unknown[]): DbMessage {
  const db = getDb();
  const tc = toolCalls ? JSON.stringify(toolCalls) : null;
  const stmt = db.prepare(
    `INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, ?, ?, ?) RETURNING *`
  );
  // Also touch session updated_at
  db.prepare("UPDATE sessions SET updated_at = datetime('now') WHERE id = ?").run(sessionId);
  return stmt.get(sessionId, role, content, tc) as DbMessage;
}

export function getMessages(sessionId: string): DbMessage[] {
  const db = getDb();
  return db
    .prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC")
    .all(sessionId) as DbMessage[];
}
