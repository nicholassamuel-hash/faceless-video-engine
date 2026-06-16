# Deploy Runbook — AI Agency Stack (Abandoned Cart Recovery Demo)

A single Ubuntu VPS running **Caddy + n8n + Flowise** in Docker, plus a
demo abandoned-cart recovery bot. ~10 minutes from blank VPS to working demo.

---

## 0. What's in this folder

| Path | Phase | Purpose |
|------|-------|---------|
| `docker-compose.yml` | 1 | Runs Caddy + n8n + Flowise (HTTP mode) |
| `docker-compose.https.yml` | — | Optional overlay for real HTTPS |
| `.env.example` | 1 | Copy to `.env`, fill in secrets |
| `caddy/Caddyfile` | 1 | Router (HTTP) |
| `caddy/Caddyfile.https` | — | Router (HTTPS) |
| `workflows/abandoned-cart-recovery.json` | 2 | n8n workflow to import |
| `demo/index.html` | 3 | Client-facing trigger form |
| `scripts/test-webhook.sh` | 4 | End-to-end test |

---

## 1. Prerequisites

- Ubuntu 20.04+ VPS, **2 GB RAM minimum**, public IP.
- Open inbound ports: **22** (SSH), **80** (web/webhook). For HTTPS also **443**.
  For the admin UIs over plain HTTP, **5678** and **3000** (lock these to your
  own IP — see step 6).
- Credentials you'll need in `.env`:
  - **LLM** — OpenRouter key (`sk-or-...`) or Groq key.
  - **SMTP** — Gmail address + 16-char App Password (see step 7b).

---

## 2. Get the files onto the VPS

From your machine (PowerShell or terminal), copy the folder up:

```bash
scp -r ai-agency user@YOUR_IP:~/ai-agency
```

Then SSH in:

```bash
ssh user@YOUR_IP
cd ~/ai-agency
```

---

## 3. Install Docker (skip if already installed)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # then log out/in so 'docker' works without sudo
```

Verify: `docker compose version`

---

## 4. Add swap (recommended on 2 GB)

n8n + Flowise + Caddy is tight on 2 GB; swap prevents OOM kills.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. Configure `.env`

```bash
cp .env.example .env
openssl rand -hex 24        # copy this for N8N_ENCRYPTION_KEY
nano .env
```

Minimum to fill for the HTTP demo:

| Variable | Value |
|----------|-------|
| `VPS_IP` | Your server's public IP |
| `N8N_ENCRYPTION_KEY` | The `openssl rand -hex 24` output (never change later) |
| `FLOWISE_PASSWORD` | A strong password |
| `LLM_API_BASE` | `https://openrouter.ai/api/v1` (or Groq's) |
| `LLM_API_KEY` | Your OpenRouter/Groq key |
| `LLM_MODEL` | `meta-llama/llama-3.3-70b-instruct` (or `llama-3.3-70b-versatile` for Groq) |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASS` | Gmail App Password |
| `SMTP_FROM_EMAIL` | Same Gmail address |

> `.env` is git-ignored — never commit it.

---

## 6. Launch (HTTP mode)

```bash
docker compose up -d
docker compose ps          # all three should be "running"
docker compose logs -f     # Ctrl-C to stop following
```

Lock down the admin UIs to your own IP (recommended):

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow from YOUR_HOME_IP to any port 5678 proto tcp
sudo ufw allow from YOUR_HOME_IP to any port 3000 proto tcp
sudo ufw enable
```

Reachable now:
- n8n editor → `http://YOUR_IP:5678`
- Flowise → `http://YOUR_IP:3000`
- Demo page → `http://YOUR_IP/` (404 until the workflow is active — fine)

---

## 7. Configure n8n

**a) Owner account** — open `http://YOUR_IP:5678`, create the first account.

**b) Gmail App Password** (if you haven't already):
1. `myaccount.google.com/security` → enable **2-Step Verification**.
2. `myaccount.google.com/apppasswords` → create one → copy the 16-char value
   into `SMTP_PASS` in `.env`, then `docker compose up -d` again to reload.

**c) Import the workflow** — in n8n: **⋯ → Import from File** →
`workflows/abandoned-cart-recovery.json`.

**d) Create the SMTP credential** — open the **Send Recovery Email** node →
it shows "Gmail SMTP" as missing → **Create new** (type **SMTP**):
- Host `smtp.gmail.com` · Port `587` · User = your Gmail · Password = App Password
- **SSL/TLS = OFF** (port 587 uses STARTTLS)

**e) Activate** the workflow (toggle, top-right). This enables the production
webhook at `/webhook/abandoned-cart`.

---

## 8. Test end-to-end

**Option A — script (from the VPS):**
```bash
chmod +x scripts/test-webhook.sh
./scripts/test-webhook.sh YOUR_IP you@gmail.com
```

**Option B — the demo form:** open `http://YOUR_IP/`, fill it in, submit.

Expected: `{"status":"scheduled", ...}` and the email arrives in ~1 minute
(the demo payload sets `delay_minutes: 1`; production cart events use 60).

---

## 9. View the log

```bash
docker compose exec n8n cat /home/node/.n8n/cart_recovery_log.csv
```
Columns: `timestamp, customer_name, customer_email, cart_total, status`.

---

## 10. Upgrade to HTTPS (free, green lock — best for recording)

Real certs need a hostname. **nip.io** gives you one with zero signup by
embedding your IP. For `203.0.113.5`, set in `.env`:

```
DEMO_HOST=app.203.0.113.5.nip.io
N8N_HOST=n8n.203.0.113.5.nip.io
FLOWISE_HOST=flowise.203.0.113.5.nip.io
ACME_EMAIL=you@example.com
```

Open port 443, then relaunch with the overlay:

```bash
sudo ufw allow 443/tcp
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d
```

Now: `https://app.<ip>.nip.io/` (demo), `https://n8n.<ip>.nip.io/` (editor),
`https://flowise.<ip>.nip.io/` (Flowise) — all with valid certificates.

> **If certs fail to issue:** nip.io shares Let's Encrypt rate limits, so it can
> occasionally be throttled. Fall back to a free **DuckDNS** subdomain
> (duckdns.org — 2-min signup), point it at your IP, and use that as your hosts.

---

## 11. Maintenance

| Task | Command |
|------|---------|
| Restart everything | `docker compose restart` |
| Stop | `docker compose down` (volumes/data preserved) |
| Update to latest images | `docker compose pull && docker compose up -d` |
| Tail logs | `docker compose logs -f n8n` |
| Back up n8n data | `docker run --rm -v ai-agency_n8n_data:/d -v $PWD:/b alpine tar czf /b/n8n-backup.tgz -C /d .` |

Data lives in named volumes (`n8n_data`, `flowise_data`, `caddy_data`) and
survives `down`/reboots. Only `docker compose down -v` wipes it.

---

## 12. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Can't log into n8n over HTTP | Already handled: `N8N_SECURE_COOKIE=false` is set for HTTP mode. If you moved to HTTPS, it flips to `true` automatically. |
| Webhook returns 404 | The workflow isn't **active**. Toggle it on. |
| Email not arriving | Check the SMTP credential (App Password, port 587, SSL off). Gmail blocks normal passwords — it must be an App Password. |
| `$env.*` empty in workflow | You edited `.env` after starting — run `docker compose up -d` to reload container env. |
| Port 80 already in use | Another web server (nginx/apache) is running. Stop it: `sudo systemctl stop nginx`. |
| Containers killed / restarting | Out of RAM — add swap (step 4). |
| LLM step errors but email still sends | By design: the email falls back to default copy if the LLM call fails. Check `LLM_API_KEY`/`LLM_MODEL`. |
