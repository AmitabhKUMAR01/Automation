# 🚀 Deployment Guide: Taking the Project Live

Deploying a FastAPI application that heavily relies on **Playwright (Chromium)** and **APScheduler** requires a robust environment because headless browsers consume significant memory and require specific OS-level dependencies.

Here is the step-by-step process for making your lead generator live.

---

## Phase 1: Choose Your Infrastructure

Because Playwright Chromium instances use substantial RAM, you should avoid "micro" instances.

- **Recommended Server (VPS)**: AWS EC2 (t3.small/t3.medium), DigitalOcean Droplet, or Hetzner Cloud.
- **Minimum Specs**: 2 CPU Cores, 4GB RAM (2GB is the absolute minimum, but might crash if you increase `MAX_CONCURRENT_PROFILES`).
- **Operating System**: Ubuntu 22.04 or 24.04 LTS (widely supported by Playwright).

> **Database Note**: You can either install MySQL directly on this server to save money, or use a Managed Database (like AWS RDS or DigitalOcean Managed MySQL) for easier backups and scalability.

---

## Phase 2: Deployment Strategy (Docker vs. Native)

You have two main paths to deploy. **Docker** is highly recommended for this project because it perfectly encapsulates Playwright's complicated OS dependencies.

### Option A: The Docker Approach (Highly Recommended)
We have already provided a `Dockerfile` and `docker-compose.yml`.
1. The `Dockerfile` uses a Python base image, installs OS dependencies for Playwright (`playwright install --with-deps chromium`), and copies your code.
2. The `docker-compose.yml` mounts the `.env` and `logs/` directory.
3. **Why?** It guarantees that if it works on your machine, it will work on the server without hunting down missing Ubuntu libraries.

### Option B: The Native Approach (Systemd)
If you prefer not to use Docker, we've provided Systemd and Nginx templates in the `deploy/` directory.
1. Clone the repo to the server (`/var/www/web-scrapping`).
2. Create a virtual environment, install requirements, and run `playwright install --with-deps chromium`.
3. Create a **Systemd Service** (using `deploy/leadgen.service`) to run `uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1` in the background.

---

## Phase 3: The Step-by-Step Process (Docker)

Assuming you go with a standard VPS and choose Docker, here is the chronological workflow:

### 1. Server Preparation
- SSH into your new VPS: `ssh root@your-server-ip`
- Update packages: `sudo apt update && sudo apt upgrade -y`
- Install Docker:
  ```bash
  sudo apt install -y docker.io docker-compose-v2
  sudo systemctl enable docker
  sudo systemctl start docker
  ```

### 2. Application Deployment
- Clone your repository to the server.
  ```bash
  git clone https://gitlab.com/ankitscode/web-scrapping.git /var/www/web-scrapping
  cd /var/www/web-scrapping
  ```
- Create your production `.env` file. **Crucial**: Ensure `DATABASE_URL`, `LLM_API_KEY`, and all other secrets are securely placed in this `.env`.
  ```bash
  nano .env
  ```
- Start the application in detached mode:
  ```bash
  docker compose up -d --build
  ```
- Check your logs to ensure the APScheduler starts successfully:
  ```bash
  docker compose logs -f
  ```

### 3. Database Setup (If using a local database container/installation)
- If using a Managed DB, get the connection string and place it in the `.env` before running docker compose.
- Run your Alembic migrations on the production database:
  ```bash
  # Enter the docker container (if it's running via Docker)
  docker compose exec web bash
  alembic upgrade head
  exit
  ```

### 4. Reverse Proxy & SSL (Nginx)
You shouldn't expose FastAPI directly on port 8000 to the internet. Instead, use **Nginx** to accept web traffic on port 80 (HTTP) and 443 (HTTPS) and forward it to FastAPI.

- Install Nginx: `sudo apt install nginx`
- Copy the provided configuration file: `sudo cp deploy/nginx.conf /etc/nginx/sites-available/leadgen`
- Enable the site: `sudo ln -s /etc/nginx/sites-available/leadgen /etc/nginx/sites-enabled/`
- Test Nginx: `sudo nginx -t` and restart: `sudo systemctl restart nginx`
- **SSL Certificate**: Install `certbot` and run `sudo certbot --nginx -d api.yourdomain.com` to automatically get a free Let's Encrypt SSL certificate and secure your API endpoints.

---

## Phase 4: Production Considerations for this Specific App

### LinkedIn Session Expirations
In production, you still need a way to upload LinkedIn session cookies to the `/profile-settings/` API. You will need to either run the login script locally and paste the JSON into your production API (via Swagger UI or `curl`), or build a secure frontend to submit the JSON string.

### Uvicorn Workers
When running FastAPI in production natively, people often use multiple workers (e.g., `uvicorn main:app --workers 4`). **DO NOT DO THIS.**
Because you are running `APScheduler` inside the same FastAPI process, running 4 workers will spawn 4 independent schedulers, resulting in duplicate emails and duplicated LinkedIn actions. **Always run exactly 1 worker.**

### Log Management
Since we just set up daily rotating logs in the `logs/` directory, they will naturally clean themselves up over time, preventing your server's hard drive from filling up.
