> # 📦 Archived
>
> **Active development has moved to
> [CaddisMaster/budget-buddy](https://github.com/CaddisMaster/budget-buddy),
> starting fresh at `v0.1.0`.**
>
> This repository is the historical record of the project's first era —
> **`v1` through `v10.15.0`** — and is kept read-only for reference. Every
> commit, tag, release note, and issue from that period lives here and is not
> going anywhere.
>
> Nothing was lost in the move. The application itself carried over intact; only
> the *envelope* around it was rebuilt: an issue → branch → PR workflow, CI and
> CD in GitHub Actions, container images on GitHub Container Registry instead of
> Docker Hub, and proper contributor documentation. The version number reset to
> `0.1.0` to reflect a `0.x` no-stability-contract scheme, replacing the old
> scheme whose MAJOR had been frozen at `10`.
>
> Looking for the history behind a particular feature? It is here. Looking for
> the code that runs today? It is
> [over there](https://github.com/CaddisMaster/budget-buddy).

# Budget Buddy

[![CI](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml/badge.svg)](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml)

A personal budget tracking and ledger system built with Python, Flask, and PostgreSQL.

## Why I Built This

I decided to build Budget Buddy because I was looking to expand my understanding of SQL, Python, and database systems. My lack of hands-on experience with these tools drove me to want to develop something new. Rather than focusing on courses I decided to build something real, that is where Budget Buddy came along!

## Live Demo

🔗 [budget.seandesmet.com](https://budget.seandesmet.com) *(requires login — personal use only)*

## Features

- **Natural-language quick add (AI)** — type a transaction in plain English ("spent 42 on groceries at Safeway yesterday") and Claude parses it into amount, category, account, and date, pre-filling the add form for you to confirm. Server-side via the Anthropic API (Claude Haiku); an assist, not autopilot — nothing is saved until you press Add
- **Monthly insight (AI)** — an on-demand digest on the dashboard: the app computes the month's figures (income, spending, top categories, budget overruns) deterministically and Claude writes a plain-English recap plus a coaching tip or two. Cached per month with a Regenerate button; the AI only narrates the numbers — it never does the math
- **Multi-user** — session-based authentication, admin-only user creation, per-user data isolation
- **Transaction tracking** — log income and expenses with categories, accounts, and dates
- **Inline editing** — edit and delete transactions, categories, accounts, budgets, and goals right in the list with HTMX, no full-page reloads
- **Scheduled income & bills** — set up recurring income and expenses on their own tab (six frequencies, including semi-monthly with two pay days); each posts a transaction automatically on its due date, going forward only — no back-dated entries on first setup
- **Account transfers** — move money between accounts in one step; both balances update and the transfer stays out of the income/expense charts
- **Savings goals** — set a target on an account with a projected completion date and an on-track / behind status, advanced automatically by transfers into that account
- **Smart budgets** — one monthly amount per category, auto-suggested from the last 6 months of spending; edit to set your own (it stays fixed) or clear to revert, with this month's actual shown inline
- **Transaction history** — search, filter by month, pagination, and a running balance column
- **CSV export** — download filtered transactions as a CSV file
- **Dashboard** — one page for the numbers: net position hero with savings rate, Chart.js visualisations (spending by category, cash flow, net balance over time, budget performance, spending by day of week), and a year-over-year comparison when a month is selected
- **Admin tools** — user management UI, database backup download via pg_dump
- **Dark mode** — automatic system-based dark mode support
- **Mobile responsive** — collapsible sidebar, horizontal table scroll, tested on iPhone

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Flask, Gunicorn |
| Database | PostgreSQL 16 |
| Frontend | Jinja2, HTMX, Chart.js, custom CSS |
| AI | Anthropic Claude API (Haiku) — natural-language entry + monthly insight digest |
| Containerisation | Docker, Docker Compose |
| Reverse proxy | Nginx |
| SSL | Let's Encrypt (Certbot) |
| Hosting | DigitalOcean Droplet |
| Registry | Docker Hub |

## Architecture

```
Mac (development)
    ↓ git push + ./deploy.sh
Docker Hub (caddismaster/budget-buddy:latest)
    ↓ docker-compose pull
DigitalOcean Droplet
    → Nginx (SSL termination, reverse proxy)
        → Gunicorn → Flask app
        → PostgreSQL (Docker volume)
```

- All code developed on Mac, built as a multi-platform Docker image, and deployed to a DigitalOcean Droplet
- Nginx handles HTTPS and routes traffic — Flask is never directly exposed
- Database lives in a Docker volume with automatic schema initialisation on first startup

## Project Structure

```
budget-buddy/
├── app/
│   ├── __init__.py       # Flask app + extensions; registers blueprints
│   ├── models.py         # User model for Flask-Login
│   ├── db.py             # Connection helper + db_cursor() context manager
│   ├── helpers.py        # Shared helpers: is_htmx(), hx_toast(), ai_enabled()
│   ├── ai.py             # Anthropic integration — NL parsing + insight digest
│   ├── blueprints/       # Routes, one module per area (auth, main,
│   │                     #   transactions, categories, accounts, budgets,
│   │                     #   admin, transfers, goals, schedules, insights)
│   ├── static/
│   │   ├── style.css     # Full stylesheet with dark mode
│   │   └── htmx.min.js   # Vendored HTMX
│   └── templates/        # Jinja2 templates (+ partials/ HTMX fragments)
├── tests/                # pytest suite (date math, routes, isolation,
│                         #   HTMX endpoints, AI parsing, schedules,
│                         #   insight, transfers, goals)
├── sql/                  # Numbered migrations + schema.sql
├── scripts/              # Data pipeline (ingest, clean, insert)
├── landing/
│   └── index.html        # Personal home page (seandesmet.com)
├── .github/workflows/    # CI — pytest on every push/PR
├── Dockerfile
├── docker-compose.yml
├── deploy.sh             # Multi-platform build and push to Docker Hub
├── test.sh               # Run the test suite in a throwaway container
├── requirements.txt
└── requirements-dev.txt  # Test dependencies (pytest)
```

## Local Setup

**Prerequisites:** Docker Desktop

```bash
git clone git@github.com:CaddisMaster/budget-buddy.git
cd budget-buddy
```

Create a `.env` file:
```
DB_HOST=db
DB_PORT=5432
DB_NAME=budget
DB_USER=admin
DB_PASSWORD=yourpassword
SECRET_KEY=yoursecretkey
```

Optionally, to enable the AI natural-language quick add, add your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-...
```
The app runs fine without it — the Quick add box simply stays hidden until a key is set.

Start the app:
```bash
docker-compose up
```

Open [http://localhost:5001](http://localhost:5001) in your browser.

The database schema initialises automatically on first startup. No manual SQL setup required. Create an initial admin user via psql after first startup:

```bash
docker exec -it budget-buddy-db-1 psql -U admin -d budget
```

```sql
INSERT INTO users (username, password_hash, is_admin)
VALUES ('admin', '<bcrypt hash>', true);
```

Generate a bcrypt hash inside the web container:
```bash
docker exec budget-buddy-web-1 python3 -c "from flask_bcrypt import Bcrypt; print(Bcrypt().generate_password_hash('yourpassword').decode())"
```

Once logged in, create additional users via Settings → Manage users.

## Database Schema

- `transactions` — id, amount, description, category_id, account_id, transaction_date, transaction_type, is_adjustment, is_transfer, transfer_group_id, user_id, created_at (the legacy `is_recurring`/`frequency`/`next_due`/`recur_second_day` columns remain but are unused — recurrence moved to the `schedules` table)
- `schedules` — id, amount, description, category_id, account_id, transaction_type, frequency, anchor_day, second_day, next_due, is_active, user_id, created_at — recurring income/expense templates; each materialises a plain transaction on its due date
- `categories` — id, name, description, user_id, created_at
- `budgets` — id, category_id, amount (one monthly amount per category, `UNIQUE(user_id, category_id)`), user_id, created_at
- `account` — account_id, account_name, type, user_id
- `goals` — id, name, target_amount, target_date, account_id, baseline_amount, user_id, created_at
- `insights` — id, year, month, content (the AI monthly digest, as JSON), model, user_id, created_at, `UNIQUE(user_id, year, month)`
- `users` — id, username, password_hash, is_admin, created_at

A `transfer_group_seq` sequence links each transfer's expense/income pair via a shared `transfer_group_id`. All data tables carry a `user_id` foreign key — every query is scoped to the logged-in user for full data isolation.

## Security

- Flask-Login session-based authentication — no public registration
- Flask-Bcrypt password hashing — passwords never stored as plain text
- Admin-only routes for settings, backup, and user management
- Flask-Limiter — 60 requests per minute per IP
- Gunicorn production WSGI server (no Flask dev server in production)
- Nginx reverse proxy — Flask port never publicly exposed
- ufw firewall — only ports 22 and Nginx Full allowed
- HTTPS via Let's Encrypt with auto-renewal
- debug=False in production

## Testing

A pytest suite covers the recurring-transaction date math, route/auth behaviour, per-user data isolation, the HTMX inline-edit endpoints, transfers and savings goals, and the AI parsing layer (with the network call mocked, so no API key is needed to run the tests). Tests run in a throwaway Docker container on the same Python as production — no local install needed, and the same suite runs in GitHub Actions on every push:

```bash
./test.sh
```
