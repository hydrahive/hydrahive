/**
 * bridge.js — HydraHive WhatsApp Bridge
 *
 * Verwaltet WhatsApp-Sessions via whatsapp-web.js (Puppeteer/Chrome).
 * Eine Session pro personal Agent.
 *
 * Env-Variablen:
 *   BRIDGE_PORT      (default: 8767)
 *   SESSIONS_DIR     (default: /etc/octopos/whatsapp-sessions)
 *   CORE_URL         (default: http://127.0.0.1:8765)
 *   BRIDGE_SECRET    (default: leer)
 */

import express from 'express'
import pkg from 'whatsapp-web.js'
const { Client, LocalAuth, MessageMedia } = pkg
import QRCode from 'qrcode'
import fs from 'fs'
import path from 'path'
import fetch from 'node-fetch'

const PORT          = parseInt(process.env.BRIDGE_PORT   || '8767')
const SESSIONS_DIR  = process.env.SESSIONS_DIR            || '/etc/octopos/whatsapp-sessions'
const CORE_URL      = process.env.CORE_URL                || 'http://127.0.0.1:8765'
const BRIDGE_SECRET = process.env.BRIDGE_SECRET          || ''

const app = express()
app.use(express.json({ limit: '10mb' }))

// sessions: agentId → { client, status, qrBase64, phone }
const sessions = new Map()

// ── Session-Verwaltung ───────────────────────────────────────────────────────

async function createSession(agentId) {
  // Alte Session aufräumen falls vorhanden
  const existing = sessions.get(agentId)
  if (existing?.client) {
    try { await existing.client.destroy() } catch {}
  }

  fs.mkdirSync(SESSIONS_DIR, { recursive: true, mode: 0o700 })

  const session = { status: 'connecting', qrBase64: null, phone: null, client: null }
  sessions.set(agentId, session)

  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: agentId,
      dataPath: SESSIONS_DIR,
    }),
    puppeteer: {
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--disable-gpu',
      ],
    },
  })

  session.client = client

  client.on('qr', async (qr) => {
    session.qrBase64 = await QRCode.toDataURL(qr)
    session.status = 'waiting_qr'
    console.log(`[${agentId}] QR-Code bereit`)
  })

  client.on('ready', () => {
    session.status = 'connected'
    session.qrBase64 = null
    session.phone = client.info?.wid?.user || ''
    console.log(`[${agentId}] Verbunden: +${session.phone}`)
  })

  client.on('authenticated', () => {
    session.status = 'authenticated'
    session.qrBase64 = null
    console.log(`[${agentId}] Authentifiziert`)
  })

  client.on('auth_failure', (msg) => {
    console.error(`[${agentId}] Authentifizierungsfehler: ${msg}`)
    session.status = 'auth_failure'
  })

  client.on('disconnected', async (reason) => {
    console.log(`[${agentId}] Verbindung getrennt: ${reason}`)
    session.status = 'disconnected'
    session.phone = null
    // Session-Daten löschen damit beim nächsten Start ein neuer QR kommt
    if (reason === 'LOGOUT') {
      sessions.delete(agentId)
    }
  })

  client.on('message', async (msg) => {
    // Nur eingehende Nachrichten (nicht eigene)
    if (msg.fromMe) return

    // Status-Broadcasts und Status-Antworten komplett ignorieren
    if (msg.from === 'status@broadcast' || msg.from?.endsWith('@broadcast')) return
    if (msg._data?.broadcast === true) return
    if (msg.type === 'e2e_notification' || msg.type === 'notification_template') return

    // LID-Auflösung: msg.from kann in neueren WA-Versionen eine interne
    // Geräte-ID (LID) statt der Telefonnummer enthalten → Kontakt auflösen
    let from = msg.from
    try {
      const contact = await msg.getContact()
      if (contact?.number) {
        from = `${contact.number}@c.us`
      }
    } catch (e) {
      console.warn(`[${agentId}] getContact fehlgeschlagen: ${e.message}`)
    }
    const fromName = msg._data?.notifyName || msg._data?.pushName || ''
    const isAudio  = msg.type === 'ptt' || msg.type === 'audio'
    const text     = msg.body?.trim()

    // Weder Text noch Audio → ignorieren
    if (!text && !isAudio) return

    // Agent-Loop-Schutz: Nachrichten von anderen HydraHive-Agenten ignorieren
    // (erkennbar am unsichtbaren Marker \u200B am Ende)
    if (text && text.includes('\u200B')) return

    let payload = { agent_id: agentId, from, from_name: fromName, message: text || '' }

    // Audio/PTT: Mediendaten herunterladen und mitsenden
    if (isAudio) {
      try {
        const media = await msg.downloadMedia()
        if (media?.data) {
          payload.media_type = media.mimetype || 'audio/ogg'
          payload.media_data = media.data  // base64
          console.log(`[${agentId}] Audio-Nachricht von ${fromName || from} (${media.mimetype}, ${Math.round(media.data.length * 3/4 / 1024)} KB)`)
        }
      } catch (e) {
        console.warn(`[${agentId}] Medien-Download fehlgeschlagen: ${e.message}`)
      }
    } else {
      console.log(`[${agentId}] Nachricht von ${fromName || from}: ${text.slice(0, 80)}`)
    }

    // An Python-Core weiterleiten
    try {
      const res = await fetch(`${CORE_URL}/internal/whatsapp/incoming`, {
        method:  'POST',
        headers: {
          'Content-Type':    'application/json',
          'X-Bridge-Secret': BRIDGE_SECRET,
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(60000),  // länger für Transkription
      })
      if (!res.ok) {
        console.warn(`[${agentId}] Core-Callback HTTP ${res.status}`)
      }
    } catch (e) {
      console.error(`[${agentId}] Core-Callback Fehler: ${e.message}`)
    }
  })

  try {
    await client.initialize()
    console.log(`[${agentId}] Client initialisiert`)
  } catch (e) {
    console.warn(`[${agentId}] Initialisierungsfehler, retry in 8s: ${e.message}`)
    session.status = 'connecting'
    try { await client.destroy() } catch {}
    sessions.delete(agentId)
    setTimeout(() => {
      createSession(agentId).catch(err =>
        console.error(`[${agentId}] Retry fehlgeschlagen: ${err.message}`)
      )
    }, 8000)
  }
  return session
}

// ── REST-API ─────────────────────────────────────────────────────────────────

// Session starten
app.post('/sessions/:agentId/start', async (req, res) => {
  const { agentId } = req.params
  const existing = sessions.get(agentId)
  if (existing && existing.status !== 'disconnected' && existing.status !== 'auth_failure') {
    return res.json({ status: existing.status, qr: existing.qrBase64, phone: existing.phone })
  }
  try {
    // createSession ist async aber wir warten nur auf den Start, nicht auf QR/Ready
    createSession(agentId).catch(e =>
      console.error(`[${agentId}] Session-Fehler: ${e.message}`)
    )
    // Kurz warten damit der erste Status gesetzt wird
    await new Promise(r => setTimeout(r, 500))
    const s = sessions.get(agentId)
    res.json({ status: s?.status || 'connecting', qr: s?.qrBase64 || null, phone: s?.phone || null })
  } catch (e) {
    console.error(`[${agentId}] Session-Start fehlgeschlagen: ${e.message}`)
    res.status(500).json({ error: e.message })
  }
})

// Status abfragen (polling für QR-Code)
app.get('/sessions/:agentId/status', (req, res) => {
  const { agentId } = req.params
  const s = sessions.get(agentId)
  if (!s) {
    // Prüfen ob gespeicherte Session-Daten existieren
    const sessionDir = path.join(SESSIONS_DIR, `session-${agentId}`)
    const hasSession = fs.existsSync(sessionDir)
    return res.json({ status: hasSession ? 'saved' : 'disconnected', qr: null, phone: null })
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
    const chatId = to.includes('@') ? to : `${to}@c.us`
    // Unsichtbaren Agent-Marker anhängen um Loops zu verhindern
    await s.client.sendMessage(chatId, message + '\u200B')
    res.json({ sent: true })
  } catch (e) {
    console.error(`[${agentId}] Senden fehlgeschlagen: ${e.message}`)
    res.status(500).json({ error: e.message })
  }
})

// Voice-Nachricht senden (PTT)
app.post('/sessions/:agentId/send-voice', async (req, res) => {
  const { agentId } = req.params
  const { to, audio_data } = req.body
  const s = sessions.get(agentId)
  if (!s || s.status !== 'connected') {
    return res.status(400).json({ error: 'Session nicht verbunden' })
  }
  try {
    const chatId = to.includes('@') ? to : `${to}@c.us`
    const media = new MessageMedia('audio/ogg; codecs=opus', audio_data)
    await s.client.sendMessage(chatId, media, { sendAudioAsVoice: true })
    res.json({ sent: true })
  } catch (e) {
    console.error(`[${agentId}] Voice-Senden fehlgeschlagen: ${e.message}`)
    res.status(500).json({ error: e.message })
  }
})

// Session trennen
app.delete('/sessions/:agentId', async (req, res) => {
  const { agentId } = req.params
  const s = sessions.get(agentId)
  if (s?.client) {
    try { await s.client.logout() } catch {}
    try { await s.client.destroy() } catch {}
  }
  sessions.delete(agentId)
  // Gespeicherte Session-Daten löschen
  const sessionDir = path.join(SESSIONS_DIR, `session-${agentId}`)
  fs.rmSync(sessionDir, { recursive: true, force: true })
  res.json({ disconnected: true })
})

// Health-Check
app.get('/health', (req, res) => {
  res.json({ ok: true, sessions: sessions.size })
})

// ── Graceful Shutdown ────────────────────────────────────────────────────────

async function shutdown() {
  console.log('Bridge wird beendet...')
  const destroyPromises = []
  for (const [id, s] of sessions.entries()) {
    destroyPromises.push(
      Promise.race([
        (async () => { try { await s.client.destroy() } catch {} })(),
        new Promise(r => setTimeout(r, 3000)),  // max 3s pro Client
      ])
    )
  }
  await Promise.all(destroyPromises)
  process.exit(0)
}

process.on('SIGTERM', shutdown)
process.on('SIGINT',  shutdown)

// ── Start ────────────────────────────────────────────────────────────────────

app.listen(PORT, '127.0.0.1', () => {
  console.log(`HydraHive WhatsApp Bridge gestartet auf Port ${PORT}`)
  // Core benachrichtigen damit Sessions neu gestartet werden
  setTimeout(async () => {
    try {
      await fetch(`${CORE_URL}/internal/whatsapp/bridge-ready`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Bridge-Secret': BRIDGE_SECRET },
        body: JSON.stringify({ port: PORT }),
        signal: AbortSignal.timeout(5000),
      })
      console.log('Core über Bridge-Start benachrichtigt')
    } catch (e) {
      console.warn(`Core-Benachrichtigung fehlgeschlagen (Core noch nicht bereit?): ${e.message}`)
    }
  }, 3000)
})
