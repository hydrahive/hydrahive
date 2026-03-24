/**
 * bridge.js — HydraHive WhatsApp Bridge
 *
 * Verwaltet WhatsApp-Sessions via Baileys (multi-session).
 * Eine Session pro personal Agent.
 *
 * Env-Variablen:
 *   BRIDGE_PORT      (default: 8767)
 *   SESSIONS_DIR     (default: /etc/octopos/whatsapp-sessions)
 *   CORE_URL         (default: http://127.0.0.1:8765)
 *   BRIDGE_SECRET    (default: leer)
 */

import express from 'express'
import { makeWASocket, useMultiFileAuthState, DisconnectReason, Browsers } from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import QRCode from 'qrcode'
import fs from 'fs'
import path from 'path'
import fetch from 'node-fetch'
import pino from 'pino'

const PORT         = parseInt(process.env.BRIDGE_PORT   || '8767')
const SESSIONS_DIR = process.env.SESSIONS_DIR            || '/etc/octopos/whatsapp-sessions'
const CORE_URL     = process.env.CORE_URL                || 'http://127.0.0.1:8765'
const BRIDGE_SECRET = process.env.BRIDGE_SECRET         || ''

const logger = pino({ level: 'info' })
const app = express()
app.use(express.json())

// sessions: agentId → { socket, status, qrBase64, phone, authDir, reconnectTimer }
const sessions = new Map()

// ── Session-Verwaltung ───────────────────────────────────────────────────────

async function createSession(agentId) {
  const authDir = path.join(SESSIONS_DIR, agentId)
  fs.mkdirSync(authDir, { recursive: true, mode: 0o700 })

  // Evtl. alten Reconnect-Timer löschen
  const existing = sessions.get(agentId)
  if (existing?.reconnectTimer) clearTimeout(existing.reconnectTimer)

  const { state, saveCreds } = await useMultiFileAuthState(authDir)
  const session = {
    status: 'connecting',
    qrBase64: null,
    phone: null,
    socket: null,
    authDir,
    reconnectTimer: null,
  }
  sessions.set(agentId, session)

  // Baileys-Logger auf silent setzen um Spam zu reduzieren
  const baileysLogger = pino({ level: 'silent' })

  const sock = makeWASocket({
    auth: state,
    browser: Browsers.ubuntu('HydraHive'),
    printQRInTerminal: false,
    logger: baileysLogger,
  })
  session.socket = sock

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      session.qrBase64 = await QRCode.toDataURL(qr)
      session.status = 'waiting_qr'
      logger.info({ agentId }, 'QR-Code bereit')
    }

    if (connection === 'open') {
      session.status = 'connected'
      session.qrBase64 = null
      session.phone = sock.user?.id?.split(':')[0] || ''
      logger.info({ agentId, phone: session.phone }, 'Verbunden')
    }

    if (connection === 'close') {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode
      const loggedOut  = statusCode === DisconnectReason.loggedOut

      logger.info({ agentId, statusCode, loggedOut }, 'Verbindung getrennt')

      if (loggedOut) {
        // Ausgeloggt → Session + Auth-Daten löschen
        sessions.delete(agentId)
        fs.rmSync(authDir, { recursive: true, force: true })
      } else {
        // Netzwerkfehler o.ä. → nach 5s neu verbinden
        session.status = 'reconnecting'
        session.socket = null
        session.reconnectTimer = setTimeout(() => {
          if (sessions.has(agentId)) createSession(agentId).catch(e =>
            logger.error({ agentId, err: e.message }, 'Reconnect fehlgeschlagen')
          )
        }, 5000)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages: msgs, type }) => {
    if (type !== 'notify') return
    for (const msg of msgs) {
      if (msg.key.fromMe) continue
      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        ''
      if (!text.trim()) continue

      const from = msg.key.remoteJid || ''
      logger.info({ agentId, from, text: text.slice(0, 80) }, 'Nachricht empfangen')

      // An Python-Core weiterleiten
      try {
        const res = await fetch(`${CORE_URL}/internal/whatsapp/incoming`, {
          method:  'POST',
          headers: {
            'Content-Type':    'application/json',
            'X-Bridge-Secret': BRIDGE_SECRET,
          },
          body: JSON.stringify({ agent_id: agentId, from, message: text }),
          signal: AbortSignal.timeout(10000),
        })
        if (!res.ok) {
          logger.warn({ agentId, status: res.status }, 'Core-Callback HTTP-Fehler')
        }
      } catch (e) {
        logger.error({ agentId, err: e.message }, 'Core-Callback Fehler')
      }
    }
  })

  logger.info({ agentId }, 'Session gestartet')
  return session
}

// ── REST-API ─────────────────────────────────────────────────────────────────

// Session starten oder bestehende abfragen
app.post('/sessions/:agentId/start', async (req, res) => {
  const { agentId } = req.params
  const existing = sessions.get(agentId)
  if (existing && existing.status !== 'logged_out') {
    return res.json({ status: existing.status, qr: existing.qrBase64, phone: existing.phone })
  }
  try {
    const s = await createSession(agentId)
    res.json({ status: s.status, qr: s.qrBase64, phone: s.phone })
  } catch (e) {
    logger.error({ agentId, err: e.message }, 'Session-Start fehlgeschlagen')
    res.status(500).json({ error: e.message })
  }
})

// Status abfragen (polling für QR-Code)
app.get('/sessions/:agentId/status', (req, res) => {
  const { agentId } = req.params
  const s = sessions.get(agentId)
  if (!s) {
    // Prüfen ob gespeicherte Credentials existieren (= war schon verbunden)
    const credsFile = path.join(SESSIONS_DIR, agentId, 'creds.json')
    const hasCreds  = fs.existsSync(credsFile)
    return res.json({ status: hasCreds ? 'saved' : 'disconnected', qr: null, phone: null })
  }
  res.json({ status: s.status, qr: s.qrBase64, phone: s.phone })
})

// Nachricht senden
app.post('/sessions/:agentId/send', async (req, res) => {
  const { agentId } = req.params
  const { to, message } = req.body
  const s = sessions.get(agentId)
  if (!s || s.status !== 'connected') {
    return res.status(400).json({ error: 'Session nicht verbunden' })
  }
  try {
    const jid = to.includes('@') ? to : `${to}@s.whatsapp.net`
    await s.socket.sendMessage(jid, { text: message })
    res.json({ sent: true })
  } catch (e) {
    logger.error({ agentId, err: e.message }, 'Senden fehlgeschlagen')
    res.status(500).json({ error: e.message })
  }
})

// Session trennen + Auth löschen
app.delete('/sessions/:agentId', async (req, res) => {
  const { agentId } = req.params
  const s = sessions.get(agentId)
  if (s) {
    if (s.reconnectTimer) clearTimeout(s.reconnectTimer)
    if (s.socket) {
      try { await s.socket.logout() } catch {}
    }
    sessions.delete(agentId)
  }
  const authDir = path.join(SESSIONS_DIR, agentId)
  fs.rmSync(authDir, { recursive: true, force: true })
  res.json({ disconnected: true })
})

// Health-Check
app.get('/health', (req, res) => {
  res.json({ ok: true, sessions: sessions.size })
})

// ── Start ────────────────────────────────────────────────────────────────────

app.listen(PORT, '127.0.0.1', () => {
  logger.info({ port: PORT }, 'HydraHive WhatsApp Bridge gestartet')
})
