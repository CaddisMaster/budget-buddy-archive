# Budget Buddy

[![CI](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml/badge.svg)](https://github.com/CaddisMaster/budget-buddy/actions/workflows/ci.yml)

A personal budget tracking and ledger system built with Python, Flask, and PostgreSQL.

## Why I Built This

I decided to build Budget Buddy because I was looking to expand my understanding of SQL, Python, and database systems. My lack of hands-on experience with these tools drove me to want to develop something new. Rather than focusing on courses I decided to build something real, that is where Budget Buddy came along!

## Live Demo

🔗 [budget.seandesmet.com](https://budget.seandesmet.com) *(requires login — personal use only)*

## Screenshots

### Login
![Login](screenshots/Login.png)

### Transaction History
![Transactions](screenshots/Transactions.png)

### Analytics
![Analytics 1](screenshots/Analytics%201.png)
![Analytics 2](screenshots/Analytics%202.png)

### Dashboard
![Dashboard 1](screenshots/Dashboard%201.png)
![Dashboard 2](screenshots/Dashboard%202.png)
![Dashboard 3](screenshots/Dashboard%203.png)

### Settings
![Settings](screenshots/Settings.png)

## Features

- **Multi-user** — session-based authentication, admin-only user creation, per-user data isolation
- **Transaction tracking** — log income and expenses with categories, accounts, and dates
- **Recurring transactions** — repeat on six frequencies (weekly, bi-weekly, semi-monthly, monthly, quarterly, annually), auto-processed on page load
- **Transaction history** — search, filter by month, pagination, and running balance column
- **CSV export** — download filtered transactions as a CSV file
- **Analytics** — savings rate, year over year comparison, spending by day of week, and budget vs actual
- **Dashboard** — Chart.js visualisations including spending by category, cash flow, net balance over time, and budget performance
- **Smart budgets** — one monthly amount per category, auto-suggested from the last 6 months of spending; edit to set your own (it stays fixed) or clear to revert, with this month's actual shown inline
- **Admin tools** — user management UI, database backup download via pg_dump
- **Dark mode** — automatic system-based dark mode support
- **Mobile responsive** — collapsible sidebar, horizontal table scroll, tested on iPhone

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, Flask, Gunicorn |
| Database | PostgreSQL 16 |
| Frontend | Jinja2, Chart.js, custom CSS |
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
│   ├── db.py             # Database connection helper
│   ├── blueprints/       # Routes, one module per area (auth, main,
│   │                     #   transactions, categories, accounts,
│   │                     #   budgets, analytics, admin)
│   ├── static/
│   │   └── style.css     # Full stylesheet with dark mode
│   └── templates/        # Jinja2 HTML templates
├── tests/                # pytest suite (date math, routes, data isolation)
├── sql/                  # Numbered migrations + schema.sql
├── scripts/              # Data pipeline (ingest, clean, insert)
├── landing/
│   └── index.html        # Personal home page (seandesmet.com)
├── screenshots/          # App screenshots for README
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

- `transactions` — id, amount, description, category_id, account_id, transaction_date, transaction_type, is_recurring, frequency, next_due, recur_second_day, is_adjustment, user_id, created_at
- `categories` — id, name, description, user_id, created_at
- `budgets` — id, category_id, amount (one monthly amount per category), user_id, created_at
- `account` — account_id, account_name, type, user_id
- `users` — id, username, password_hash, is_admin, created_at

All data tables carry a `user_id` foreign key — every query is scoped to the logged-in user for full data isolation.

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

A pytest suite covers the recurring-transaction date math, route/auth behaviour, and per-user data isolation. Tests run in a throwaway Docker container on the same Python as production — no local install needed:

```bash
./test.sh
```
