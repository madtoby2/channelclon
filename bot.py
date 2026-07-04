#!/usr/bin/env python3
"""
Telegram Channel Forwarder Bot
Bot (PTB) + Telethon user account (media forwarding)
"""
import os, sys, re, json, asyncio, logging, tempfile, traceback, random, string
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(format='%(asctime)s [%(name)s] %(levelname)s %(message)s', level=logging.INFO)
logger = logging.getLogger('forwarder')

# ── Config from environment ────────────────────────────

API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
BOT_TOKEN = os.environ['BOT_TOKEN']
OWNER_ID = int(os.environ['OWNER_ID'])
CONFIG_PATH = os.environ.get('CONFIG_PATH', 'fwd_config.json')
SESSION_PATH = os.environ.get('SESSION_PATH', 'forwarder.session')
CONTACT_LINK = os.environ.get('CONTACT_LINK', 'https://t.me/username')

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters


# ── Config persistence ─────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {
        'source': '', 'target': '', 'status': 'idle', 'forwarded_ids': [],
        'blocked_extensions': [], 'blocked_texts': [],
        'date_from': '', 'date_to': '',
        'auto_interval': 0, 'quiet_start': '', 'quiet_end': '',
        'fetch_comments': False, 'verified_users': [], 'codes': {},
    }


def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def _clean_input(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r'https?://t\.me/', '', raw)
    raw = re.sub(r'https?://telegram\.me/', '', raw)
    raw = raw.lstrip('@').strip()
    return raw


async def _resolve_entity(client, name):
    try:
        return await client.get_entity(int(name))
    except (ValueError, TypeError):
        pass
    if name.startswith('@'):
        name = name[1:]
    if name.startswith('+') or '/+' in name:
        if not name.startswith('http'):
            name = 'https://t.me/' + name
        return await client.get_entity(name)
    return await client.get_entity(name)


# ── Code-based access control ──────────────────────────

def _is_verified(user_id: int) -> bool:
    """Owner always passes; others must have verified via code."""
    if user_id == OWNER_ID:
        return True
    cfg = load_config()
    return user_id in cfg.get('verified_users', [])


def _generate_code() -> str:
    """Generate a unique short code."""
    cfg = load_config()
    existing = set(cfg.get('codes', {}).keys())
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if code not in existing:
            break
    codes = cfg.get('codes', {})
    codes[code] = None
    cfg['codes'] = codes
    save_config(cfg)
    return code


async def _verify_code(code: str, user_id: int) -> bool:
    """Redeem a code. Returns True on success."""
    cfg = load_config()
    codes = cfg.get('codes', {})
    if code not in codes:
        return False
    if codes[code] is not None:
        return False
    codes[code] = user_id
    verified = set(cfg.get('verified_users', []))
    verified.add(user_id)
    cfg['verified_users'] = sorted(verified)
    cfg['codes'] = codes
    save_config(cfg)
    return True


# ── Core forwarding logic ──────────────────────────────

async def forward_all(source, target, already_forwarded, limit=0):
    """Download media via Telethon user session and re-upload to target."""
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        logger.error('Telethon session not authorized')
        await client.disconnect()
        return 0

    try:
        src = await _resolve_entity(client, source)
        dst = await _resolve_entity(client, target)
        src_title = getattr(src, 'title', None) or source
        logger.info(f'Forwarding: {src_title} → {target}')

        kwargs = {}
        if limit > 0:
            kwargs['limit'] = limit

        all_msgs = [m async for m in client.iter_messages(src, **kwargs)]
        all_msgs.reverse()

        cfg = load_config()
        unforwarded = [m for m in all_msgs if m.id not in already_forwarded]

        # Date filtering
        if cfg.get('date_from'):
            try:
                df = datetime.strptime(cfg['date_from'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                unforwarded = [m for m in unforwarded if m.date >= df]
            except ValueError:
                logger.warning('Invalid date_from: %s', cfg['date_from'])
        if cfg.get('date_to'):
            try:
                dt = datetime.strptime(cfg['date_to'], '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
                unforwarded = [m for m in unforwarded if m.date <= dt]
            except ValueError:
                logger.warning('Invalid date_to: %s', cfg['date_to'])

        blocked_ext = [e.lower() for e in cfg.get('blocked_extensions', [])]

        def _is_blocked(m):
            if not blocked_ext:
                return False
            if m.document and m.file and m.file.name:
                ext = Path(m.file.name).suffix.lower()
                if ext in blocked_ext:
                    logger.info('  Skipping blocked file #%d: %s (%s)', m.id, m.file.name, ext)
                    return True
            return False

        blocked_texts = [t.lower() for t in cfg.get('blocked_texts', [])]

        def _is_text_blocked(m):
            if not blocked_texts:
                return False
            text = (m.text or m.message or '').lower()
            for kw in blocked_texts:
                if kw in text:
                    logger.info('  Skipping blocked text #%d: contains "%s"', m.id, kw)
                    return True
            return False

        # Discussion group comment forwarding
        _discussion_group = None

        async def _fetch_and_forward_comments(msg_id):
            nonlocal _discussion_group
            if not cfg.get('fetch_comments'):
                return
            try:
                if _discussion_group is None:
                    from telethon.tl.functions.channels import GetFullChannelRequest
                    full = await client(GetFullChannelRequest(channel=src))
                    linked_id = getattr(full.full_chat, 'linked_chat_id', None)
                    if not linked_id:
                        _discussion_group = False
                        return
                    _discussion_group = await client.get_entity(linked_id)
                    logger.info('Discussion group: %s', getattr(_discussion_group, 'title', linked_id))

                if _discussion_group is False:
                    return

                async for reply in client.iter_messages(_discussion_group, reply_to=msg_id, limit=20):
                    sender = await reply.get_sender()
                    if sender is None:
                        display_name = 'Anonymous'
                    elif hasattr(sender, 'first_name'):
                        parts = [sender.first_name or '']
                        if getattr(sender, 'last_name', None):
                            parts.append(sender.last_name)
                        display_name = ' '.join(parts).strip() or 'User'
                        if getattr(sender, 'username', None):
                            display_name += f' (@{sender.username})'
                    else:
                        display_name = getattr(sender, 'title', 'User') or 'User'
                        if getattr(sender, 'username', None):
                            display_name += f' (@{sender.username})'
                    text = reply.text or ''
                    if not text.strip():
                        continue
                    comment_text = f'💬 {display_name}:\n{text}'
                    await client.send_message(dst, comment_text)
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug('Failed to fetch comments #%d: %s', msg_id, e)

        count = 0
        processed_groups = set()

        for m in unforwarded:
            if m.grouped_id:
                if m.grouped_id in processed_groups:
                    continue
                group_msgs = [x for x in unforwarded if x.grouped_id == m.grouped_id]
                processed_groups.add(m.grouped_id)

                caption = ''
                for gm in group_msgs:
                    if gm.text:
                        caption = gm.text
                files = []
                for gm in group_msgs:
                    if not (gm.photo or gm.video):
                        continue
                    ext = '.jpg'
                    if gm.video:
                        ext = '.mp4'
                        if gm.file and gm.file.name:
                            ext = Path(gm.file.name).suffix or '.mp4'
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix=f'grp_{m.grouped_id}_{gm.id}_')
                    tmp.close()
                    fp = await gm.download_media(file=tmp.name)
                    if fp and os.path.getsize(fp) > 0:
                        files.append(fp)
                if files:
                    try:
                        await client.send_file(dst, files, caption=caption)
                    except Exception as e:
                        logger.error(f'group#{m.grouped_id} send failed: {e}')
                    for fp in files:
                        try:
                            os.unlink(fp)
                        except:
                            pass
                    for gm in group_msgs:
                        already_forwarded.add(gm.id)
                        count += 1
                    cfg = load_config()
                    cfg['forwarded_ids'] = sorted(already_forwarded)[-10000:]
                    save_config(cfg)
                    await asyncio.sleep(0.3)
                continue
            else:
                if _is_blocked(m):
                    already_forwarded.add(m.id)
                    count += 1
                    continue
                if _is_text_blocked(m):
                    already_forwarded.add(m.id)
                    count += 1
                    continue
                try:
                    if m.photo:
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg', prefix=f'fwd_{m.id}_')
                        tmp.close()
                        fp = await m.download_media(file=tmp.name)
                        if fp and os.path.getsize(fp) > 0:
                            await client.send_file(dst, fp, caption=m.text or '', force_document=False)
                        os.unlink(fp) if fp and os.path.exists(fp) else None
                    elif m.video:
                        ext = '.mp4'
                        if m.file and m.file.name:
                            ext = Path(m.file.name).suffix or '.mp4'
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix=f'fwd_{m.id}_')
                        tmp.close()
                        fp = await m.download_media(file=tmp.name)
                        if fp and os.path.getsize(fp) > 0:
                            kw = {
                                'caption': m.text or '',
                                'force_document': False,
                                'supports_streaming': True,
                            }
                            if m.video and hasattr(m.video, 'attributes'):
                                kw['attributes'] = m.video.attributes
                            await client.send_file(dst, fp, **kw)
                        os.unlink(fp) if fp and os.path.exists(fp) else None
                    elif m.document:
                        orig_name = m.file.name if m.file and m.file.name else None
                        suffix = Path(orig_name).suffix if orig_name else '.bin'
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix=f'fwd_{m.id}_')
                        tmp.close()
                        fp = await m.download_media(file=tmp.name)
                        if fp and os.path.getsize(fp) > 0:
                            kw = {'caption': m.text or '', 'file_name': orig_name, 'force_document': True}
                            if m.document and hasattr(m.document, 'attributes'):
                                kw['attributes'] = m.document.attributes
                            await client.send_file(dst, fp, **kw)
                        os.unlink(fp) if fp and os.path.exists(fp) else None
                    elif m.text:
                        await client.send_message(dst, m.text)

                    await _fetch_and_forward_comments(m.id)

                    count += 1
                    already_forwarded.add(m.id)
                    cfg = load_config()
                    cfg['forwarded_ids'] = sorted(already_forwarded)[-10000:]
                    save_config(cfg)
                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.error(f'msg#{m.id} forward failed: {e}')
                    logger.error(traceback.format_exc())

        logger.info(f'Done: forwarded {count} messages')
        return count
    finally:
        await client.disconnect()


# ── Bot command handlers ───────────────────────────────

def _is_quiet_hours(cfg) -> bool:
    qs = cfg.get('quiet_start', '')
    qe = cfg.get('quiet_end', '')
    if not qs or not qe:
        return False
    try:
        sh, sm = map(int, qs.split(':'))
        eh, em = map(int, qe.split(':'))
    except (ValueError, TypeError):
        return False
    now = datetime.now(timezone.utc)
    nmin = now.hour * 60 + now.minute
    smin = sh * 60 + sm
    emin = eh * 60 + em
    if smin <= emin:
        return smin <= nmin <= emin
    return nmin >= smin or nmin <= emin


async def _auto_forward_tick(context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    if not cfg.get('source') or not cfg.get('target'):
        return
    if cfg.get('status') == 'forwarding':
        logger.info('⏭️ Auto: previous forward still running, skip')
        return
    if _is_quiet_hours(cfg):
        logger.info('🌙 Auto: quiet hours, skip')
        return
    already = set(cfg.get('forwarded_ids', []))
    count = await forward_all(cfg['source'], cfg['target'], already, limit=0)
    if count > 0:
        logger.info(f'Auto: forwarded {count} new messages')


def _setup_auto_job(app, interval_minutes: int):
    existing = app.bot_data.get('auto_job')
    if existing:
        try:
            existing.schedule_removal()
        except Exception:
            pass
    if interval_minutes <= 0:
        app.bot_data['auto_job'] = None
        logger.info('Auto: cancelled scheduled forwarding')
        return
    job = app.job_queue.run_repeating(
        _auto_forward_tick,
        interval=interval_minutes * 60,
        first=15,
    )
    app.bot_data['auto_job'] = job
    logger.info('Auto: started scheduled forwarding (every %d min)', interval_minutes)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_verified(user_id):
        await update.message.reply_text(
            '🔑 <b>Channel Forwarder Bot</b>\n\n'
            'Send your verification code to activate:\n'
            f'<code>/verify XXXXXXXX</code>\n\n'
            f"No code yet? Contact {CONTACT_LINK}")
        return

    cfg = load_config()
    lines = ['🤖 <b>Channel Forwarder Bot</b>']
    lines.append('')
    lines.append(f'Source: {cfg.get("source") or "not set"}')
    lines.append(f'Target: {cfg.get("target") or "not set"}')
    lines.append(f'Status: {cfg.get("status", "idle")}')
    lines.append(f'Forwarded: {len(cfg.get("forwarded_ids", []))} msgs')
    ai = cfg.get('auto_interval', 0)
    if ai > 0:
        lines.append(f'⏱️ Auto: every {ai} min')
        qs = cfg.get('quiet_start', '')
        qe = cfg.get('quiet_end', '')
        if qs and qe:
            lines.append(f'🌙 Quiet: {qs} ~ {qe}')
    lines.append('')
    lines.append('<b>Commands:</b>')
    lines.extend([
        '/source @xxx — set source channel',
        '/target @xxx — set target channel',
        '/block_ext .zip .rar — block file extensions',
        '/unblock_ext .zip — unblock extension',
        '/block_text keywords — block messages containing keywords',
        '/unblock_text keyword — unblock keyword',
        '/list_blocked — show all block rules',
        '/date_range YYYY-MM-DD YYYY-MM-DD — date filter',
        '/last_days 7 — last N days',
        '/auto 30 — auto-forward every N min',
        '/auto_off — disable auto-forward',
        '/quiet 23:00 08:00 — quiet hours',
        '/quiet_off — disable quiet hours',
        '/comments on — enable comment forwarding',
        '/comments off — disable comment forwarding',
        '/clear — reset all settings',
        '/status — show current state',
    ])

    blocked = cfg.get('blocked_extensions', [])
    texts = cfg.get('blocked_texts', [])
    df = cfg.get('date_from', '')
    dt = cfg.get('date_to', '')
    if blocked or texts or df:
        lines.append('')
        lines.append('<b>Active filters:</b>')
        if blocked:
            lines.append(f'🚫 Extensions: {" ".join(blocked)}')
        if texts:
            lines.append(f'📝 Keywords: {", ".join(texts)}')
        if df:
            lines.append(f'📅 Date: {df}' + (f' ~ {dt}' if dt else ' onwards'))

    if cfg.get('fetch_comments'):
        lines.append('💬 Comments capture: ✅ enabled')

    verified = _is_verified(user_id)
    btns = []
    if cfg.get('source') and cfg.get('target'):
        btns.append(InlineKeyboardButton('📥 Recent 30', callback_data='fwd_recent'))
        if verified:
            btns.append(InlineKeyboardButton('📦 All history', callback_data='fwd_all'))
    btns.append(InlineKeyboardButton('🗑️ Clear', callback_data='fwd_clear'))

    await update.message.reply_text(
        '\n'.join(lines), parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([btns] if len(btns) <= 3 else [[b] for b in btns])
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = update.effective_user.id
    if not _is_verified(user_id):
        await q.answer('🔑 Verify first — send /verify CODE', show_alert=True)
        return
    await q.answer()
    data = q.data
    cfg = load_config()

    if data == 'fwd_clear':
        save_config({
            'source': '', 'target': '', 'status': 'idle', 'forwarded_ids': [],
            'blocked_extensions': [], 'blocked_texts': [],
            'date_from': '', 'date_to': '',
            'auto_interval': 0, 'quiet_start': '', 'quiet_end': '',
            'fetch_comments': False,
            'verified_users': cfg.get('verified_users', []),
            'codes': cfg.get('codes', {}),
        })
        await q.edit_message_text('✅ Cleared all settings')
        app = context.application
        if app and app.bot_data:
            existing = app.bot_data.get('auto_job')
            if existing:
                try:
                    existing.schedule_removal()
                except Exception:
                    pass

    elif data in ('fwd_recent', 'fwd_all'):
        if data == 'fwd_all' and not _is_verified(user_id):
            await q.edit_message_text('❌ Only verified users can forward all history')
            return
        if not cfg.get('source') or not cfg.get('target'):
            await q.edit_message_text('❌ Set source and target first')
            return
        if cfg.get('status') in ('forwarding',):
            await q.edit_message_text('⏳ Already forwarding, please wait')
            return
        cfg['status'] = 'forwarding'
        save_config(cfg)
        limit = 30 if data == 'fwd_recent' else 0
        await q.edit_message_text('🔄 Forward started in background')
        already = set(cfg.get('forwarded_ids', []))
        asyncio.create_task(
            _bg_forward(cfg['source'], cfg['target'], already, limit=limit, notify_chat=q.message.chat_id)
        )


async def _bg_forward(source, target, already, limit=0, notify_chat=None):
    from telegram import Bot
    try:
        count = await forward_all(source, target, already, limit=limit)
        cfg = load_config()
        cfg['status'] = 'idle'
        save_config(cfg)
        if notify_chat:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(chat_id=notify_chat, text=f'✅ Forwarded: {count} messages')
    except Exception as e:
        logger.error(f'Forward failed: {e}')
        logger.error(traceback.format_exc())
        cfg = load_config()
        cfg['status'] = 'idle'
        save_config(cfg)
        if notify_chat:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(chat_id=notify_chat, text=f'❌ Forward failed: {e}')


# ── Configuration commands ─────────────────────────────

async def set_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /source @channel')
        return
    raw = ' '.join(context.args)
    clean = _clean_input(raw)
    if not clean:
        await update.message.reply_text('❌ Cannot parse: ' + raw)
        return
    cfg = load_config()
    cfg['source'] = clean
    cfg['forwarded_ids'] = []
    save_config(cfg)
    await update.message.reply_text('✅ Source set: ' + clean + '\n👉 Now set target (/target @xxx)')


async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /target @channel')
        return
    raw = ' '.join(context.args)
    clean = _clean_input(raw)
    if not clean:
        await update.message.reply_text('❌ Cannot parse: ' + raw)
        return
    cfg = load_config()
    cfg['target'] = clean
    save_config(cfg)
    await update.message.reply_text('✅ Target set: ' + clean + '\n👉 Send /start to begin')


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    save_config({
        'source': '', 'target': '', 'status': 'idle', 'forwarded_ids': [],
        'blocked_extensions': [], 'blocked_texts': [],
        'date_from': '', 'date_to': '',
        'auto_interval': 0, 'quiet_start': '', 'quiet_end': '',
        'fetch_comments': False,
        'verified_users': cfg.get('verified_users', []),
        'codes': cfg.get('codes', {}),
    })
    _setup_auto_job(context.application, 0)
    await update.message.reply_text('✅ Cleared all settings')


async def cmd_block_ext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        blocked = ', '.join(load_config().get('blocked_extensions', [])) or 'none'
        await update.message.reply_text(f'Usage: /block_ext .zip .rar\nCurrent: {blocked}')
        return
    cfg = load_config()
    blocked = cfg.get('blocked_extensions', [])
    added = []
    for a in context.args:
        ext = a.lower().strip()
        if not ext.startswith('.'):
            ext = '.' + ext
        if ext not in blocked:
            blocked.append(ext)
            added.append(ext)
    cfg['blocked_extensions'] = blocked
    save_config(cfg)
    if added:
        await update.message.reply_text(f'✅ Blocked: {", ".join(added)}\nCurrent: {", ".join(blocked) or "none"}')
    else:
        await update.message.reply_text(f'ℹ️ Already in block list\nCurrent: {", ".join(blocked) or "none"}')


async def cmd_unblock_ext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /unblock_ext .zip .rar')
        return
    cfg = load_config()
    blocked = cfg.get('blocked_extensions', [])
    removed = []
    for a in context.args:
        ext = a.lower().strip()
        if not ext.startswith('.'):
            ext = '.' + ext
        if ext in blocked:
            blocked.remove(ext)
            removed.append(ext)
    cfg['blocked_extensions'] = blocked
    save_config(cfg)
    if removed:
        await update.message.reply_text(f'✅ Unblocked: {", ".join(removed)}\nCurrent: {", ".join(blocked) or "none"}')
    else:
        await update.message.reply_text('These extensions are not in the block list')


async def cmd_list_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    blocked = cfg.get('blocked_extensions', [])
    texts = cfg.get('blocked_texts', [])
    lines = []
    if blocked:
        lines.append(f'🚫 Blocked extensions: {" ".join(blocked)}')
    if texts:
        lines.append(f'📝 Blocked keywords: {", ".join(texts)}')
    if not lines:
        await update.message.reply_text('📭 No block rules set')
    else:
        await update.message.reply_text('\n'.join(lines))


async def cmd_block_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        blocked = ', '.join(load_config().get('blocked_texts', [])) or 'none'
        await update.message.reply_text(f'Usage: /block_text ad spam\nCurrent: {blocked}')
        return
    cfg = load_config()
    texts = cfg.get('blocked_texts', [])
    added = []
    for kw in context.args:
        kw = kw.strip().lower()
        if kw and kw not in texts:
            texts.append(kw)
            added.append(kw)
    cfg['blocked_texts'] = texts
    save_config(cfg)
    if added:
        await update.message.reply_text(f'✅ Blocked keywords: {", ".join(added)}\nCurrent: {", ".join(texts) or "none"}')
    else:
        await update.message.reply_text('These keywords are already blocked')


async def cmd_unblock_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /unblock_text ad')
        return
    cfg = load_config()
    texts = cfg.get('blocked_texts', [])
    removed = []
    for kw in context.args:
        kw = kw.strip().lower()
        if kw in texts:
            texts.remove(kw)
            removed.append(kw)
    cfg['blocked_texts'] = texts
    save_config(cfg)
    if removed:
        await update.message.reply_text(f'✅ Unblocked: {", ".join(removed)}\nCurrent: {", ".join(texts) or "none"}')
    else:
        await update.message.reply_text('These keywords are not in the block list')


async def cmd_date_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text('Usage: /date_range 2024-01-01 2024-12-31')
        return
    date_from = context.args[0]
    date_to = context.args[1] if len(context.args) > 1 else ''
    try:
        datetime.strptime(date_from, '%Y-%m-%d')
    except ValueError:
        await update.message.reply_text(f'❌ Invalid date: {date_from}, use YYYY-MM-DD')
        return
    if date_to:
        try:
            datetime.strptime(date_to, '%Y-%m-%d')
        except ValueError:
            await update.message.reply_text(f'❌ Invalid date: {date_to}, use YYYY-MM-DD')
            return
    cfg = load_config()
    cfg['date_from'] = date_from
    cfg['date_to'] = date_to
    save_config(cfg)
    msg = f'✅ Date range: {date_from}'
    if date_to:
        msg += f' ~ {date_to}'
    await update.message.reply_text(msg)


async def cmd_last_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /last_days 7')
        return
    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text('❌ Enter a number')
        return
    if days <= 0:
        await update.message.reply_text('❌ Days must be > 0')
        return
    from datetime import timedelta
    date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
    cfg = load_config()
    cfg['date_from'] = date_from
    cfg['date_to'] = ''
    save_config(cfg)
    await update.message.reply_text(f'✅ Last {days} days ({date_from} ~ now)')


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    lines = ['📊 <b>Current Status</b>']
    lines.append('')
    lines.append(f'Source: {cfg.get("source") or "not set"}')
    lines.append(f'Target: {cfg.get("target") or "not set"}')
    lines.append(f'Status: {cfg.get("status", "idle")}')
    lines.append(f'Forwarded: {len(cfg.get("forwarded_ids", []))} msgs')
    if cfg.get('fetch_comments'):
        lines.append('💬 Comments capture: ✅ enabled')
    blocked = cfg.get('blocked_extensions', [])
    texts = cfg.get('blocked_texts', [])
    if blocked:
        lines.append(f'🚫 Extensions: {" ".join(blocked)}')
    if texts:
        lines.append(f'📝 Keywords: {", ".join(texts)}')
    df = cfg.get('date_from', '')
    dt = cfg.get('date_to', '')
    if df:
        if dt:
            lines.append(f'📅 Range: {df} ~ {dt}')
        else:
            lines.append(f'📅 From: {df}')
    await update.message.reply_text('\n'.join(lines), parse_mode='HTML')


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /auto 30 (every 30 min)')
        return
    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text('❌ Enter a number')
        return
    if minutes < 5:
        await update.message.reply_text('❌ Minimum interval: 5 min')
        return
    cfg = load_config()
    cfg['auto_interval'] = minutes
    save_config(cfg)
    _setup_auto_job(context.application, minutes)
    await update.message.reply_text(f'✅ Auto-forward every {minutes} minutes')


async def cmd_auto_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    cfg['auto_interval'] = 0
    save_config(cfg)
    _setup_auto_job(context.application, 0)
    await update.message.reply_text('⏹️ Auto-forward disabled')


async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text('Usage: /quiet 23:00 08:00')
        return
    qs, qe = context.args[0], context.args[1]
    for t in (qs, qe):
        try:
            h, m = map(int, t.split(':'))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(f'❌ Invalid time: {t}, use HH:MM')
            return
    cfg = load_config()
    cfg['quiet_start'] = qs
    cfg['quiet_end'] = qe
    save_config(cfg)
    await update.message.reply_text(f'✅ Quiet hours: {qs} ~ {qe}')


async def cmd_quiet_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    cfg['quiet_start'] = ''
    cfg['quiet_end'] = ''
    save_config(cfg)
    await update.message.reply_text('✅ Quiet hours disabled')


async def cmd_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        cfg = load_config()
        state = '✅ enabled' if cfg.get('fetch_comments') else '❌ disabled'
        await update.message.reply_text(f'Comments capture: {state}\nUsage: /comments on or /comments off')
        return
    cmd = context.args[0].lower()
    if cmd in ('on', '1', 'true', 'yes'):
        cfg = load_config()
        cfg['fetch_comments'] = True
        save_config(cfg)
        await update.message.reply_text('✅ Comments capture enabled')
    elif cmd in ('off', '0', 'false', 'no'):
        cfg = load_config()
        cfg['fetch_comments'] = False
        save_config(cfg)
        await update.message.reply_text('✅ Comments capture disabled')
    else:
        await update.message.reply_text('Usage: /comments on or /comments off')


# ── Code management (owner only) ───────────────────────

async def cmd_gencode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text('❌ Owner only')
        return
    count = 1
    if context.args:
        try:
            count = max(1, min(int(context.args[0]), 20))
        except ValueError:
            pass
    codes = [_generate_code() for _ in range(count)]
    lines = ['✅ Codes generated:']
    for c in codes:
        lines.append(f'<code>{c}</code>')
    if count > 1:
        lines.append(f'Total: {count}')
    await update.message.reply_text('\n'.join(lines), parse_mode='HTML')


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if _is_verified(user_id):
        await update.message.reply_text('✅ Already verified. Send /start to begin')
        return
    if not context.args:
        await update.message.reply_text('Usage: /verify XXXXXXXX')
        return
    code = context.args[0].strip().upper()
    ok = await _verify_code(code, user_id)
    if ok:
        await update.message.reply_text(f'✅ Verified! Send /start to begin')
    else:
        await update.message.reply_text(f'❌ Invalid or already used code\nContact {CONTACT_LINK}')


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text('❌ Owner only')
        return
    if not context.args:
        await update.message.reply_text('Usage: /revoke USER_ID')
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text('❌ Enter numeric user ID')
        return
    if target == OWNER_ID:
        await update.message.reply_text('❌ Cannot revoke owner')
        return
    cfg = load_config()
    verified = cfg.get('verified_users', [])
    if target not in verified:
        await update.message.reply_text(f'ℹ️ User {target} not verified')
        return
    verified.remove(target)
    cfg['verified_users'] = sorted(verified)
    save_config(cfg)
    await update.message.reply_text(f'✅ Revoked user {target}')


async def cmd_list_verified(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text('❌ Owner only')
        return
    cfg = load_config()
    verified = cfg.get('verified_users', [])
    codes = cfg.get('codes', {})
    if not verified:
        await update.message.reply_text('📭 No verified users')
        return
    lines = ['📋 <b>Verified Users</b>']
    for uid in verified:
        label = '👑 owner' if uid == OWNER_ID else ''
        lines.append(f'• <code>{uid}</code> {label}')
    lines.append(f'Total: {len(verified)}')
    used = sum(1 for v in codes.values() if v is not None)
    unused = sum(1 for v in codes.values() if v is None)
    lines.append(f'Codes: {used} used, {unused} unused')
    await update.message.reply_text('\n'.join(lines), parse_mode='HTML')


# ── Message handler (auto-detect links) ────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_verified(user_id):
        await update.message.reply_text(f'🔑 Verify first. Send /start for instructions')
        return
    text = update.message.text.strip()
    cfg = load_config()
    for token in text.split():
        t = _clean_input(token)
        if t:
            if not cfg.get('source'):
                cfg['source'] = t
                cfg['forwarded_ids'] = []
                save_config(cfg)
                await update.message.reply_text('✅ Source set: ' + t + '\n👉 Now send target link, or /target @xxx')
                return
            elif not cfg.get('target'):
                cfg['target'] = t
                save_config(cfg)
                await update.message.reply_text('✅ Target set: ' + t + '\n👉 Send /start to begin')
                return
    await update.message.reply_text('❌ Cannot parse: ' + text)


# ── Entry point ────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CommandHandler('source', set_source))
    app.add_handler(CommandHandler('target', set_target))
    app.add_handler(CommandHandler('clear', cmd_clear))
    app.add_handler(CommandHandler('block_ext', cmd_block_ext))
    app.add_handler(CommandHandler('unblock_ext', cmd_unblock_ext))
    app.add_handler(CommandHandler('list_blocked', cmd_list_blocked))
    app.add_handler(CommandHandler('block_text', cmd_block_text))
    app.add_handler(CommandHandler('unblock_text', cmd_unblock_text))
    app.add_handler(CommandHandler('date_range', cmd_date_range))
    app.add_handler(CommandHandler('last_days', cmd_last_days))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CommandHandler('help', start_cmd))
    app.add_handler(CommandHandler('auto', cmd_auto))
    app.add_handler(CommandHandler('auto_off', cmd_auto_off))
    app.add_handler(CommandHandler('quiet', cmd_quiet))
    app.add_handler(CommandHandler('quiet_off', cmd_quiet_off))
    app.add_handler(CommandHandler('comments', cmd_comments))
    app.add_handler(CommandHandler('gencode', cmd_gencode))
    app.add_handler(CommandHandler('verify', cmd_verify))
    app.add_handler(CommandHandler('revoke', cmd_revoke))
    app.add_handler(CommandHandler('list_verified', cmd_list_verified))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info('Forwarder Bot started, polling...')

    cfg = load_config()
    if cfg.get('auto_interval', 0) > 0:
        _setup_auto_job(app, cfg['auto_interval'])
        logger.info('Restored auto-forward (every %d min)', cfg['auto_interval'])

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
