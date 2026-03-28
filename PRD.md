# Todo-MD — Project PRD/Brief

**Version:** 1.0
**Date:** 2026-03-28
**Author:** Adam / Smee
**Status:** Draft

---

### 1. Executive Summary

A lightweight, web-based markdown todo app for the Smoke & Oakum team. Users authenticate via magic link (email), create/manage todo lists in markdown format, and import/export `.md` files. Lists persist server-side so nothing is lost when tabs close. Designed to be dead simple — no accounts to manage, no passwords, just email-and-go.

---

### 2. Problem Statement

The team uses markdown todo lists across various tools (text files, notes apps, chat). These are ephemeral — close a tab, lose the list. There's no shared, persistent place for project-specific or personal todo lists that's lighter than a full project management tool. Adam needs a way to spin up a quick todo list for any project, keep it alive, and optionally share it with team members.

---

### 3. Goals & Objectives

- **Business Goals:** Reduce friction in task tracking across projects. Give the team a tool that's faster than Copper tasks but more persistent than chat messages.
- **Product Objectives:**
  - Any team member can create a persistent todo list in under 30 seconds
  - Lists survive tab/browser close indefinitely
  - Import existing `.md` files from local disk
  - Export lists back to `.md` for portability
  - Clear per-user ownership via magic link auth

---

### 4. Target Audience

- **Primary Users:** Adam, Nick, Sasha, and other S&O team members who need quick project-scoped todo lists
- **Secondary Users:** Smee (automated — can create/update lists via API for heartbeat-generated action items)

---

### 5. Scope

**In-Scope (MVP — current build):**
- Magic link authentication (email-based, no passwords)
- Create, edit, delete todo lists (markdown format)
- Interactive checkboxes (`[ ]` / `[x]` toggle)
- Import `.md` files from local filesystem
- Export lists as `.md` files
- Server-side persistence (SQLite + flat files)
- Hosted on dev server at `/todo-md/`

**Out-of-Scope (future):**
- Shared/collaborative lists (multiple users editing one list)
- Real-time sync across devices
- Notifications/reminders for due items
- Tags, priorities, or filtering
- Mobile app
- Integration with Copper CRM tasks
- Smee auto-populating lists from meeting transcripts (future automation)

---

### 6. Key Features

**Feature 1: Magic Link Auth**
- User enters email, receives a link, clicks it, authenticated
- Session persists via URL token (bookmarkable)
- No passwords, no accounts to manage
- 15-minute link expiry

**Feature 2: Todo List CRUD**
- Create new named lists
- Edit markdown content directly
- Toggle checkboxes by clicking
- Delete lists
- Auto-save on changes

**Feature 3: Import/Export**
- Import: Load any `.md` file from local disk into the app
- Export: Download any list as a `.md` file
- Preserves markdown formatting round-trip

**Feature 4: Multi-List Management**
- Sidebar/list showing all user's todo lists
- Create new lists with custom names
- Switch between lists

---

### 7. User Journey

1. User visits `http://89.117.62.130/todo-md/`
2. Enters email address, clicks "Send Link"
3. Checks email, clicks magic link
4. Lands in app with their todo lists (or empty state if new)
5. Creates a new list or imports an existing `.md` file
6. Edits todos — checkboxes toggle live, content auto-saves
7. Can export any list as `.md` or share the URL
8. Closes tab — comes back later, everything is still there

---

### 8. Technical Considerations

- **Backend:** Python stdlib (http.server + sqlite3 + smtplib) — zero dependencies
- **Storage:** SQLite for auth, flat `.md` files per user for list content
- **Hosting:** Dev server (89.117.62.130), systemd service, nginx reverse proxy at `/todo-md/`
- **SMTP:** Gmail SMTP via adam@smokeandoakum.co app password (server is whitelisted)
- **Design:** Single HTML file, minimal CSS, no build step, no frameworks
- **Security:** Token-based access (UUID4), magic links expire in 15 min, filename sanitization

---

### 9. Success Metrics

- Team members actually use it (>1 active user within first week)
- Lists persist reliably (zero data loss incidents)
- Magic link auth works consistently (no failed sends)
- Adam stops losing todo lists to closed tabs

---

### 10. Assumptions & Constraints

**Assumptions:**
- Team members have email addresses that receive mail
- Dev server stays online and accessible
- Gmail SMTP continues working with current app password

**Constraints:**
- Dev server is shared infrastructure — keep resource usage minimal
- No Node.js on server — Python stdlib only
- Single-file frontend (no build tooling)
- SMTP whitelisting tied to dev server IP

---

### 11. Open Questions

- Should lists be shareable between users? (Currently each user's lists are private)
- Should Smee auto-create project todo lists from meeting transcripts?
- Does the team want due dates / priority markers on items?
- Should there be a "team lists" view vs. "my lists"?

---

### 12. Future Considerations

- **Collaborative lists:** Share a list URL so multiple team members can edit
- **Smee integration:** Auto-populate project lists from Tactiq transcripts and daily briefings
- **Copper sync:** Two-way sync between todo items and Copper tasks
- **Notifications:** Email digest of overdue items
- **Templates:** Pre-built list templates for common project types (e.g., "New Client Onboarding", "Post-Production Checklist")
