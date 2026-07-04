# ChannelClon — Telegram 频道自动转发 Bot

自动将一个 Telegram 频道的消息搬运到另一个频道/私聊，保留原图原视频，支持过滤、评论区抓取。

## 功能

- 📥 **自动转发** — 设好源和目标频道，新消息自动搬运
- 🎯 **智能过滤** — 按文件后缀、关键词、日期范围屏蔽垃圾
- 💬 **评论区搬运** — 帖子评论区也一并带走
- 🌙 **静默时段** — 设定时间段不自动转发
- 🔑 **验证码准入** — 主人生成一次性码分发给用户，用完即止
- 📦 **批量转发** — 最近 30 条或全部历史

## 快速开始

```bash
pip install -r requirements.txt
```

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `API_ID` | ✅ | Telegram API ID（[my.telegram.org](https://my.telegram.org/apps)） |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | @BotFather 创建的 Bot Token |
| `OWNER_ID` | ✅ | 你的 Telegram 用户 ID（数字） |
| `CONFIG_PATH` | ❌ | 配置文件路径（默认 `./fwd_config.json`） |
| `SESSION_PATH` | ❌ | Telethon session 路径（默认 `./forwarder.session`） |
| `CONTACT_LINK` | ❌ | 未验证用户的联系链接（默认 `https://t.me/username`） |

### Telethon Session

需要一个已登录的 Telethon session 文件：

```python
from telethon import TelegramClient
client = TelegramClient('forwarder', API_ID, API_HASH)
await client.start()
# 登录一次后 session 文件就生成了
```

## 使用

```
python bot.py
```

给 bot 发 `/start` 查看控制面板。

### 用户命令

| 命令 | 说明 |
|------|------|
| `/verify CODE` | 用验证码激活使用权 |
| `/start` | 显示控制面板 |
| `/source @频道` | 设置源频道 |
| `/target @频道` | 设置目标频道/私聊 |
| `/block_ext .zip .rar` | 屏蔽文件后缀 |
| `/unblock_ext .zip` | 解除后缀屏蔽 |
| `/block_text 广告 推广` | 屏蔽含关键词的消息 |
| `/unblock_text 广告` | 解除关键词屏蔽 |
| `/list_blocked` | 查看当前屏蔽规则 |
| `/last_days 7` | 只转发最近 N 天 |
| `/date_range YYYY-MM-DD YYYY-MM-DD` | 按日期范围过滤 |
| `/auto 30` | 启用自动转发（每 N 分钟） |
| `/auto_off` | 关闭自动转发 |
| `/quiet 23:00 08:00` | 设置静默时段 |
| `/quiet_off` | 取消静默时段 |
| `/comments on` | 开启评论区抓取 |
| `/comments off` | 关闭评论区抓取 |
| `/clear` | 清空所有设定 |
| `/status` | 查看当前状态 |

### 主人命令

| 命令 | 说明 |
|------|------|
| `/gencode [N]` | 生成 N 个验证码 |
| `/revoke USER_ID` | 撤销某用户的访问权限 |
| `/list_verified` | 查看已验证用户和码的统计 |

## 工作原理

1. **验证码准入** — 用户通过 `/verify ABC12345` 输入一次性验证码激活使用权限
2. **主人发码** — 用 `/gencode` 生成码，分发给需要的人
3. **Telethon 用户账号转发** — bot 用已登录的 Telegram 账号下载源频道的原图原视频，再重新上传到目标频道（不是转发引用链接，所以画质无损）
4. **媒体组处理** — 相册中的多张图片/视频作为一个整体发送
5. **定时自动转发** — 按设定间隔检查新消息并自动搬运
6. **多层过滤** — 文件后缀、关键词、日期范围，按需配置

---

## 关于云码台

🔗 **[云码台 — yunmatai.xyz](https://yunmatai.xyz)**

全球短信验证码接收平台，覆盖 Telegram、OpenAI、Twitter/X、Google 等数百个服务，200+ 国家/地区号码。

- ✨ **按次付费，无需预存** — 付一单用一个号
- 🚀 **自动换号** — 没收到码自动换新号
- 💳 **微信/支付宝支付**
- 📱 **API 接入** — 开发友好

做 Telegram 账号、养号、自动化？云码台是你的号码搭档。

## License

AGPL-3.0
