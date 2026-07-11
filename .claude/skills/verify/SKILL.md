---
name: verify
description: Drive Budget Buddy's real HTTP surface at localhost:5001 to verify a change end-to-end (build, throwaway login, curl the flows, clean up).
---

# Verifying Budget Buddy changes at the real surface

## Build + launch

```bash
docker compose up -d --build web    # override builds web from local source
# app: http://localhost:5001 ; dev db container must be up (it usually is)
```

## Get a logged-in session (throwaway user)

No open registration — create the user directly (bcrypt via the app's own
extension), then curl with a cookie jar. CSRF is ON in the real app.

```bash
docker compose exec -T web python -c "
from app import app, bcrypt
import app.db as db
h = bcrypt.generate_password_hash('verify-pass-123').decode()
conn = db.get_db_connection(); cur = conn.cursor()
cur.execute(\"DELETE FROM users WHERE username='__verify__'\")
cur.execute(\"INSERT INTO users (username, password_hash) VALUES ('__verify__', %s) RETURNING id\", (h,))
print(cur.fetchone()[0]); conn.commit(); cur.close(); conn.close()"

JAR=$SCRATCHPAD/cookies.txt
TOKEN=$(curl -s -c $JAR http://localhost:5001/login | grep -o 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
curl -s -b $JAR -c $JAR -d "username=__verify__&password=verify-pass-123&csrf_token=$TOKEN" http://localhost:5001/login
```

## CSRF gotchas

- Full-page forms embed `name="csrf_token"` — re-scrape it from the page you're posting from.
- HTMX endpoints (no form on page, e.g. `/insights/generate`, `/ask`) take the
  token from the `hx-headers` attribute on `<body>`:
  ```bash
  TOKEN=$(curl -s -b $JAR http://localhost:5001/dashboard | grep -o 'X-CSRFToken[": ]*[^"}]*' | head -1 | sed 's/.*: *//' | tr -d '"')
  curl -s -b $JAR -H "HX-Request: true" -H "X-CSRFToken: $TOKEN" -d "..." http://localhost:5001/<route>
  ```
- Send `HX-Request: true` to get the fragment (row partial) instead of a redirect.

## Useful flows

- Account create: POST `/accounts` (`name`, `type` = exact `Credit Card|Debit Card|Bank Account`, `credit_limit`) → returns the `_account_row` fragment under HTMX.
- Transaction: POST `/transactions/new` (`amount`, `description`, `transaction_date`, `account_id`, `transaction_type`) → 302.
- Live AI (real key in `.env`, calls are cheap Haiku): `/ask` with `question=`, `/insights/generate` with `year`+`month`.

## Clean up (FK-safe: children first)

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
DELETE FROM transactions WHERE user_id=(SELECT id FROM users WHERE username='"'"'__verify__'"'"');
DELETE FROM insights WHERE user_id=(SELECT id FROM users WHERE username='"'"'__verify__'"'"');
DELETE FROM account WHERE user_id=(SELECT id FROM users WHERE username='"'"'__verify__'"'"');
DELETE FROM users WHERE username='"'"'__verify__'"'"';"'
```

Don't touch the `sean` account's ~6-month demo data — it's intentional.
New `sql/` migration in the diff? Apply it to the dev DB first:
`docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/NN_x.sql`
