"""webhook-sender — HTTP Webhooks als Agent-Tool."""
import json, urllib.request, urllib.error

def register(api):
    @api.tool(tool_id="send_webhook", description="Sendet einen HTTP Webhook (POST) an eine URL. Für Benachrichtigungen, Slack, Discord, Mattermost etc.",
        parameters={"type":"object","properties":{"url":{"type":"string","description":"Ziel-URL"},"payload":{"type":"object","description":"JSON-Payload"},"method":{"type":"string","enum":["POST","PUT","PATCH"],"description":"HTTP-Methode (default: POST)"},"headers":{"type":"object","description":"Zusätzliche Headers (optional)"}},"required":["url"]})
    def send_webhook(url:str, payload:dict=None, method:str="POST", headers:dict=None, **_) -> str:
        h={"Content-Type":"application/json","User-Agent":"HydraHive-Webhook/1.0"}
        if headers: h.update(headers)
        data=json.dumps(payload or {}).encode() if payload else None
        req=urllib.request.Request(url,data=data,headers=h,method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as r:
                body=r.read().decode("utf-8",errors="replace")[:2000]
                return f"✅ {r.status} {r.reason}\n\n{body}"
        except urllib.error.HTTPError as e:
            body=e.read().decode()[:500] if e.fp else ""
            return f"❌ {e.code} {e.reason}\n{body}"
        except Exception as e:
            return f"❌ Fehler: {e}"

    @api.tool(tool_id="send_discord_webhook", description="Sendet eine Nachricht an einen Discord Webhook.",
        parameters={"type":"object","properties":{"webhook_url":{"type":"string","description":"Discord Webhook URL"},"content":{"type":"string","description":"Nachricht"},"username":{"type":"string","description":"Bot-Name (optional)"}},"required":["webhook_url","content"]})
    def send_discord_webhook(webhook_url:str, content:str, username:str="HydraHive", **_) -> str:
        payload={"content":content,"username":username}
        data=json.dumps(payload).encode()
        req=urllib.request.Request(webhook_url,data=data,headers={"Content-Type":"application/json"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=10) as r:
                return f"✅ Discord-Nachricht gesendet"
        except Exception as e:
            return f"❌ Fehler: {e}"

    @api.tool(tool_id="send_slack_webhook", description="Sendet eine Nachricht an einen Slack Webhook.",
        parameters={"type":"object","properties":{"webhook_url":{"type":"string","description":"Slack Webhook URL"},"text":{"type":"string","description":"Nachricht"},"channel":{"type":"string","description":"Channel (optional)"}},"required":["webhook_url","text"]})
    def send_slack_webhook(webhook_url:str, text:str, channel:str="", **_) -> str:
        payload={"text":text}
        if channel: payload["channel"]=channel
        data=json.dumps(payload).encode()
        req=urllib.request.Request(webhook_url,data=data,headers={"Content-Type":"application/json"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=10) as r:
                return f"✅ Slack-Nachricht gesendet"
        except Exception as e:
            return f"❌ Fehler: {e}"
