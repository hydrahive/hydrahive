"""conversation-export — Chats als Markdown exportieren."""
import json, time
from pathlib import Path

AGENTS_DIR = Path("/agents")
PROJECTS_DIR = Path("/projects")

def register(api):
    @api.tool(tool_id="export_chat", description="Exportiert eine Agent- oder Projekt-Konversation als Markdown-Datei.",
        parameters={"type":"object","properties":{"agent_id":{"type":"string","description":"Agent-ID (für Agent-Chats)"},"project_id":{"type":"string","description":"Projekt-ID (für Projekt-Chats)"},"output":{"type":"string","description":"Ausgabe-Pfad (default: /tmp/export_<id>.md)"}},"required":[]})
    def export_chat(agent_id:str="",project_id:str="",output:str="",**_) -> str:
        # Session-Datei finden
        if agent_id:
            session_dir=AGENTS_DIR/agent_id/"sessions"
            ident=agent_id
        elif project_id:
            session_dir=PROJECTS_DIR/project_id
            ident=project_id
        else:
            return "agent_id oder project_id angeben"
        
        # Aktuellste Session suchen
        messages=[]
        if session_dir.exists():
            for f in sorted(session_dir.glob("*.json"),reverse=True):
                try:
                    data=json.loads(f.read_text())
                    if isinstance(data,list): messages=data; break
                    elif isinstance(data,dict) and "messages" in data: messages=data["messages"]; break
                except: continue
        
        # Session aus session.json
        if not messages:
            session_file=session_dir.parent/"session.json" if agent_id else session_dir/"session.json"
            if session_file.exists():
                try:
                    data=json.loads(session_file.read_text())
                    messages=data if isinstance(data,list) else data.get("messages",[])
                except: pass
        
        if not messages:
            return f"Keine Konversation gefunden für {ident}"
        
        # Markdown generieren
        ts=time.strftime("%Y-%m-%d %H:%M")
        md=[f"# Chat Export: {ident}",f"Exportiert: {ts}","",f"Nachrichten: {len(messages)}","","---",""]
        for m in messages:
            role=m.get("role","?")
            content=m.get("content","")
            if role=="system": continue
            if isinstance(content,list): content=" ".join(c.get("text","") for c in content if isinstance(c,dict))
            icon="👤" if role=="user" else "🤖" if role=="assistant" else "🔧"
            md.append(f"### {icon} {role.title()}")
            md.append("")
            md.append(content)
            md.append("")
            md.append("---")
            md.append("")
        
        result="\n".join(md)
        out_path=output or f"/tmp/export_{ident}_{int(time.time())}.md"
        Path(out_path).write_text(result,encoding="utf-8")
        return f"✅ Exportiert: {out_path}\n{len(messages)} Nachrichten, {len(result)} Zeichen"

    @api.tool(tool_id="list_sessions", description="Listet verfügbare Chat-Sessions eines Agents oder Projekts.",
        parameters={"type":"object","properties":{"agent_id":{"type":"string","description":"Agent-ID"},"project_id":{"type":"string","description":"Projekt-ID"}},"required":[]})
    def list_sessions(agent_id:str="",project_id:str="",**_) -> str:
        if agent_id:
            base=AGENTS_DIR/agent_id
        elif project_id:
            base=PROJECTS_DIR/project_id
        else:
            return "agent_id oder project_id angeben"
        sessions=[]
        for f in sorted(base.rglob("*.json"),reverse=True):
            if "session" in f.name.lower() or f.parent.name=="sessions":
                try:
                    size=f.stat().st_size
                    age=int((time.time()-f.stat().st_mtime)/3600)
                    sessions.append(f"{f.name} — {size//1024}KB — {age}h alt")
                except: pass
        return "\n".join(sessions[:20]) if sessions else "Keine Sessions gefunden"
