<div align="center">

# 🍭 CandyPop Bot

<div align="center">
  <img src=".github/assets/readme_banner.png" alt="CandyPop Bot Banner" width="100%"/>
</div>

**A fully-featured, production-ready Telegram bot for automated VPN subscription sales — powered by X-UI.**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://aiogram.dev)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

> Sell VPN subscriptions 24/7 on autopilot. Manage pricing, users, payments, and panels — all from Telegram.

</div>

---

## ✨ Features

### 🛒 For Users
| Feature | Description |
|---|---|
| **Buy Subscriptions** | Choose volume (GB), duration (1/2/3 months), and number of concurrent users |
| **Free Trial** | One-time test subscription with configurable GB and duration |
| **Subscription Management** | View active subs, check remaining traffic & expiry, renew or extend |
| **Wallet System** | Top-up balance via card transfer; pay instantly from wallet |
| **Referral Program** | Earn a configurable % commission on every purchase made by invited friends |
| **Dynamic Pricing** | Volume-tiered pricing — the more you buy, the less you pay per GB |
| **Discount Codes** | Apply discount codes at checkout for reduced prices |
| **QR Code Links** | Receive subscription config links with a scannable QR code |
| **Connection Guide** | In-bot guide for Android, iOS, and Windows VPN clients |
| **24/7 Support** | Dedicated support contact linked directly in the bot |

### 🛡️ For Admins
| Feature | Description |
|---|---|
| **Full Admin Panel** | Manage everything from within Telegram — no web UI needed |
| **Role-based Access** | Owner + multiple admins with granular per-feature permissions |
| **Pricing Control** | Set base GB rate, volume discount tiers, duration surcharges, and per-user fees — live |
| **Subscription Management** | Browse, search, edit, renew, delete, or disable any user subscription |
| **Manual Subscription Creation** | Create custom subs for any user directly from the panel |
| **Inbound Group Management** | Organize X-UI inbounds into named groups; control which are used for new subs |
| **Payment Review** | Approve or reject card payments with receipt image review |
| **Broadcast Messaging** | Send formatted messages (text/image/video/file) to all users at once |
| **Discount Code Manager** | Create manual or auto-generated discount codes, set % and max-uses |
| **Referral Config** | Enable/disable the referral system and set the commission percentage |
| **Test Sub Config** | Set the trial GB, duration, and per-user cooldown |
| **Alert Scheduler** | Automatic expiry/low-traffic warnings sent to users at configurable thresholds |
| **IP Checker Scheduler** | Periodic check of X-UI panel reachability; notifies admins if the panel goes down |
| **Card Config** | Update the payment card number and holder name from Telegram |
| **Channel Guard Middleware** | Force users to join a Telegram channel before using the bot |

---

## 🧱 Architecture

```
candypop_bot/
├── bot.py                      # Entry point — bot setup, routers, schedulers
├── config.py                   # All env-driven configuration
├── handlers/                   # aiogram routers (one file per feature)
│   ├── start.py                # /start, welcome, referral link parsing
│   ├── buy.py                  # Full subscription purchase flow
│   ├── subscriptions.py        # Subscription management for users
│   ├── wallet.py               # Wallet top-up flow
│   ├── referral.py             # Referral link & stats
│   ├── pricing.py              # Pricing display
│   ├── profile.py              # User profile & balance
│   ├── test_sub.py             # Free trial flow
│   ├── admin.py                # Admin panel entry
│   ├── admin_control.py        # Pricing, discounts, referral, alerts config
│   ├── admin_sub_manage.py     # Per-subscription CRUD for admins
│   ├── admin_create_sub.py     # Manual subscription creation
│   └── admin_broadcast.py      # Mass messaging
├── services/
│   ├── xui_api.py              # Async X-UI REST API client (httpx)
│   ├── pricing.py              # Dynamic pricing engine
│   ├── alert_scheduler.py      # Background: expiry & traffic alerts
│   ├── ip_checker_scheduler.py # Background: panel health check
│   └── test_sub_config.py      # Trial config persistence
├── db/
│   ├── database.py             # aiosqlite connection management
│   ├── models.py               # All DB queries (users, invoices, subs...)
│   └── discounts.py            # Discount code validation
├── keyboards/                  # Inline & reply keyboard builders
├── middlewares/
│   └── channel_check.py        # Mandatory channel membership guard
└── utils/                      # Formatting, helpers, digit conversion
```

**Tech Stack:** Python 3.11 · [aiogram 3](https://aiogram.dev) · aiosqlite · httpx · Docker

---

## 🚀 Quick Start

### Prerequisites
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A running [3x-ui panel](https://github.com/MHSanaei/3x-ui) with API access enabled
- Docker & Docker Compose (recommended)

### 1. Clone the Repository

```bash
git clone https://github.com/SeydEf/CandyPop-Bot.git
cd CandyPop-Bot
```

### 2. Configure Environment

```bash
cp .env.example .env
nano .env
```

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `BOT_NAME` | ✅ | Display name of the bot (e.g. `CandyPop`) |
| `ADMIN_CHAT_ID` | ✅ | Your Telegram user ID (owner account) |
| `CHANNEL_ID` | ✅ | Channel users must join (e.g. `@mychannel`) |
| `CHANNEL_LINK` | ✅ | Full URL to the channel |
| `XUI_BASE_URL` | ✅ | Full URL to your X-UI panel (with base path) |
| `XUI_API_TOKEN` | ✅ | X-UI API bearer token |
| `SUB_BASE_URL` | ✅ | Base URL for subscription links |
| `PROXY_URL` | ➖ | Optional HTTP proxy for bot requests |

### 3. Run with Docker Compose

```bash
docker compose up -d --build
```

Check logs:
```bash
docker compose logs -f
```

---

## 🔄 CI/CD — Automated Deployment

The repository includes a ready-to-use GitHub Actions workflow at `.github/workflows/deploy.yml`.

**On every push to `main` it will:**
1. **Test** — Build the Docker image and verify the container starts successfully
2. **Deploy** — SSH into your server and redeploy via `docker compose up -d --build`

### Setup

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `SERVER_HOST` | Your server's IP address or hostname |
| `SERVER_USER` | SSH username (e.g. `root` or `ubuntu`) |
| `SSH_PRIVATE_KEY` | Your private SSH key (contents of `~/.ssh/id_rsa`) |

Once configured, pushing to `main` will automatically test and redeploy the bot. 🎉

> **Note:** Make sure the repo is cloned on your server at `~/CandyPop-Bot` and the `.env` file is present.

---

## ⚙️ Running Without Docker

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
python bot.py
```

---

## 🎛️ Admin Panel

Send `/admin` to open the panel. The owner (`ADMIN_CHAT_ID`) has full access. Sub-admins can be added with per-section permissions.

---

## 🔔 Smart Alert System

The bot runs two background schedulers:

- **Alert Scheduler** — automatically notifies users when their subscription is about to expire or their remaining data is running low. Thresholds and check intervals are configurable live from the admin panel.
- **IP Checker Scheduler** — periodically pings the X-UI panel and alerts admins if it becomes unreachable.

---

## 🌍 Localization

The bot UI is fully in **Persian (Farsi)** with:
- RTL message formatting
- Persian numeral conversion (`۱۲۳` instead of `123`)
- Jalali (Solar Hijri) calendar support via `jdatetime`

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `aiogram` | Async Telegram Bot framework |
| `aiosqlite` | Async SQLite database driver |
| `httpx` | Async HTTP client for X-UI API |
| `httpx[socks]` | SOCKS proxy support |
| `aiohttp-socks` | SOCKS proxy support for bot session |
| `python-dotenv` | `.env` file loading |
| `qrcode[pil]` | QR code generation for subscription links |
| `Pillow` | Image processing |
| `jdatetime` | Jalali (Persian) calendar conversion |

---

## 🤝 Contributing

Contributions, issues and feature requests are welcome!

1. Fork the repository
2. Create your branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'feat: add amazing feature'`
4. Push: `git push origin feature/amazing-feature`
5. Open a Pull Request

---

## 📄 License

This project is licensed under the **Apache License 2.0**.

---

<div align="center">

Made with 🍭 and ❤️ — **CandyPop Bot**

</div>
