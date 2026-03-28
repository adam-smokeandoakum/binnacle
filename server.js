import express from 'express';
import Database from 'better-sqlite3';
import { randomUUID } from 'crypto';
import nodemailer from 'nodemailer';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3456;
const BASE_URL = process.env.BASE_URL || `http://localhost:${PORT}`;

// --- Database ---
const DB_PATH = process.env.DB_PATH || join(__dirname, 'data', 'todo.db');
import { mkdirSync } from 'fs';
mkdirSync(dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS lists (
    token TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (token, name)
  );
  CREATE TABLE IF NOT EXISTS magic_links (
    code TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER DEFAULT 0
  );
`);

// --- SMTP ---
let transporter = null;
if (process.env.SMTP_HOST) {
  transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: parseInt(process.env.SMTP_PORT || '587'),
    secure: process.env.SMTP_SECURE === 'true',
    auth: process.env.SMTP_USER ? {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS
    } : undefined
  });
}

const SMTP_FROM = process.env.SMTP_FROM || 'Todo <noreply@smokeandoakum.co>';

// --- Middleware ---
app.use(express.json());
app.use(express.text({ type: 'text/plain' }));
app.use(express.static(__dirname));

// --- Auth: Magic Link ---
app.post('/api/auth/send-link', async (req, res) => {
  const { email } = req.body;
  if (!email || !email.includes('@')) {
    return res.status(400).json({ error: 'Valid email required' });
  }

  const emailLower = email.toLowerCase().trim();

  // Get or create user
  let user = db.prepare('SELECT * FROM users WHERE email = ?').get(emailLower);
  if (!user) {
    const token = randomUUID();
    db.prepare('INSERT INTO users (email, token) VALUES (?, ?)').run(emailLower, token);
    user = { email: emailLower, token };
  }

  // Create magic link
  const code = randomUUID();
  const expiresAt = new Date(Date.now() + 15 * 60 * 1000).toISOString(); // 15 min
  db.prepare('INSERT INTO magic_links (code, email, expires_at) VALUES (?, ?, ?)').run(code, emailLower, expiresAt);

  const magicUrl = `${BASE_URL}/api/auth/verify/${code}`;

  if (transporter) {
    try {
      await transporter.sendMail({
        from: SMTP_FROM,
        to: emailLower,
        subject: 'Your Todo Link',
        text: `Click to sign in:\n\n${magicUrl}\n\nThis link expires in 15 minutes.`,
        html: `
          <div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px">
            <h2 style="font-size:20px">📋 Todo</h2>
            <p>Click the button to sign in to your todo lists:</p>
            <a href="${magicUrl}" style="display:inline-block;padding:12px 24px;background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px;margin:16px 0">Open My Todos</a>
            <p style="font-size:12px;color:#999">This link expires in 15 minutes.</p>
          </div>
        `
      });
      res.json({ ok: true, message: 'Magic link sent! Check your email.' });
    } catch (err) {
      console.error('Email send error:', err);
      res.status(500).json({ error: 'Failed to send email. Try again.' });
    }
  } else {
    // No SMTP configured — return link directly (dev mode)
    console.log(`Magic link for ${emailLower}: ${magicUrl}`);
    res.json({ ok: true, message: 'Magic link sent!', dev_link: magicUrl });
  }
});

app.get('/api/auth/verify/:code', (req, res) => {
  const { code } = req.params;
  const link = db.prepare('SELECT * FROM magic_links WHERE code = ? AND used = 0').get(code);

  if (!link) {
    return res.status(400).send('Invalid or expired link.');
  }

  if (new Date(link.expires_at) < new Date()) {
    db.prepare('UPDATE magic_links SET used = 1 WHERE code = ?').run(code);
    return res.status(400).send('Link expired. Request a new one.');
  }

  // Mark used
  db.prepare('UPDATE magic_links SET used = 1 WHERE code = ?').run(code);

  // Get user token
  const user = db.prepare('SELECT token FROM users WHERE email = ?').get(link.email);
  if (!user) {
    return res.status(400).send('User not found.');
  }

  // Redirect to app with token
  const appPath = new URL(BASE_URL).pathname.replace(/\/$/, '');
  res.redirect(`${appPath}/?t=${user.token}`);
});

// --- API: Lists ---
app.get('/api/new', (req, res) => {
  const token = randomUUID();
  res.json({ token });
});

app.get('/api/lists/:token', (req, res) => {
  const { token } = req.params;
  const files = db.prepare('SELECT name, updated_at FROM lists WHERE token = ? ORDER BY updated_at DESC').all(token);
  res.json({ files });
});

app.get('/api/lists/:token/:name', (req, res) => {
  const { token, name } = req.params;
  const row = db.prepare('SELECT content FROM lists WHERE token = ? AND name = ?').get(token, name);
  res.type('text/plain').send(row ? row.content : '');
});

app.put('/api/lists/:token/:name', (req, res) => {
  const { token, name } = req.params;
  const content = typeof req.body === 'string' ? req.body : '';
  db.prepare(`
    INSERT INTO lists (token, name, content, updated_at) VALUES (?, ?, ?, datetime('now'))
    ON CONFLICT(token, name) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
  `).run(token, name, content);
  res.json({ ok: true });
});

app.delete('/api/lists/:token/:name', (req, res) => {
  const { token, name } = req.params;
  db.prepare('DELETE FROM lists WHERE token = ? AND name = ?').run(token, name);
  res.json({ ok: true });
});

// --- Catch-all: serve index.html ---
app.get('*', (req, res) => {
  res.sendFile(join(__dirname, 'index.html'));
});

// --- Start ---
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Todo server running on ${BASE_URL}`);
  if (!transporter) {
    console.log('⚠️  No SMTP configured — magic links will be logged to console');
  }
});
