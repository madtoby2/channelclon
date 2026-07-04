# ChannelClon — Telegram Channel Forwarder Bot

[中文文档 🇨🇳](README_CN.md)

Automatically forward messages from one Telegram channel to another, with media preservation, filtering, and comment forwarding.

## Features

- 📥 **Auto-forward** — set source & target, new messages arrive automatically
- 🎯 **Smart filtering** — block by file extension, keyword, or date range
- 💬 **Comment capture** — forwards discussion group replies alongside posts
- 🌙 **Quiet hours** — skip auto-forward during specified times
- 🔑 **Code-based access** — owner generates one-time codes for users
- 📦 **Bulk forward** — forward recent 30 or all historical messages

## Requirements

- Python 3.10+
- A **Telegram API ID & Hash** (from https://my.telegram.org/apps)
- A **Telegram Bot Token** (from @BotFather)
- A **Telethon session file** (logged-in user account for forwarding)

## Setup

```bash
pip install -r requirements.txt
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | ✅ | Telegram API ID |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `OWNER_ID` | ✅ | Your Telegram user ID (numeric) |
| `CONFIG_PATH` | ❌ | Config file path (default: `./fwd_config.json`) |
| `SESSION_PATH` | ❌ | Telethon session file path (default: `./forwarder.session`) |
| `CONTACT_LINK` | ❌ | Contact link for unverified users (default: `https://t.me/username`) |

### Telethon Session

You need an authenticated Telethon session file. Generate one:

```python
from telethon import TelegramClient
client = TelegramClient('forwarder', API_ID, API_HASH)
await client.start()
# login once, session file is created
```

## Usage

```
python channel_forwarder.py
```

Send `/start` to your bot to see the control panel.

### User Commands

| Command | Description |
|---------|-------------|
| `/verify CODE` | Activate access with a one-time code |
| `/start` | Show control panel |
| `/source @channel` | Set source channel |
| `/target @channel` | Set target channel/chat |
| `/block_ext .zip .rar` | Block file extensions |
| `/unblock_ext .zip` | Unblock file extension |
| `/block_text keyword` | Block messages containing keyword |
| `/unblock_text keyword` | Unblock keyword |
| `/list_blocked` | Show all active blocks |
| `/last_days 7` | Only forward last N days |
| `/date_range YYYY-MM-DD YYYY-MM-DD` | Date range filter |
| `/auto 30` | Enable auto-forward every N minutes |
| `/auto_off` | Disable auto-forward |
| `/quiet 23:00 08:00` | Set quiet hours |
| `/quiet_off` | Disable quiet hours |
| `/comments on` | Enable comment forwarding |
| `/comments off` | Disable comment forwarding |
| `/clear` | Reset all settings |
| `/status` | Show current state |

### Owner Commands

| Command | Description |
|---------|-------------|
| `/gencode [N]` | Generate N one-time verification codes |
| `/revoke USER_ID` | Revoke a user's access |
| `/list_verified` | List all verified users and code stats |

## How It Works

1. **Access control**: users must verify with a one-time code (`/verify ABC12345`)
2. **Owner generates codes** via `/gencode` and distributes them
3. **Bot uses a Telethon user session** to download media from source and re-upload to target (preserves original quality — no forwarded-from links)
4. **Media groups** are handled atomically — all photos/videos in an album are sent together
5. **Auto-forward** runs on a cron-like timer, checking for new messages
6. **Filters** are applied before forwarding: blocked extensions, keywords, date ranges

---

### 🔗 [云码台 — yunmatai.xyz](https://yunmatai.xyz)

Global SMS verification platform — 200+ countries, hundreds of services (Telegram, OpenAI, Google, etc.). Pay-per-use, auto-refill, WeChat/Alipay. Perfect companion for Telegram account automation.

## License

AGPL-3.0
