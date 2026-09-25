# relay/relay_server.py  v3
# Multi-tenant: segmenta tráfico por company_id.
# Los monitores se autentican con JWT. Los agentes envían su company_id.

import asyncio
import websockets
import json
import os
import struct
import jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "cambiar_en_produccion_32chars_min")

# ─── ESTADO GLOBAL ────────────────────────────────────────────────────────────
agents        = {}   # hostname → { ws, company_id, info }
monitors      = set()
subscriptions = {}   # monitor_ws → set(hostnames)
monitor_co    = {}   # monitor_ws → company_id


async def handle_connection(websocket):
    try:
        first_msg = await asyncio.wait_for(websocket.recv(), timeout=15)
        data = json.loads(first_msg)
        role = data.get("role")
        if role == "agent":
            await handle_agent(websocket, data)
        elif role == "monitor":
            await handle_monitor(websocket, data)
        else:
            await websocket.close()
    except Exception:
        pass


async def handle_agent(websocket, hello: dict):
    hostname   = hello.get("hostname", "unknown")
    company_id = hello.get("company_id", "unknown")
    info       = hello.get("info", {})

    agents[hostname] = {"ws": websocket, "company_id": company_id, "info": info}
    print(f"[+] Agente: {hostname}  empresa: {company_id}")

    await broadcast_company(company_id, {
        "type": "agent_connected", "hostname": hostname, "info": info,
    })

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                hn_bytes = hostname.encode("utf-8")
                prefixed = struct.pack(">H", len(hn_bytes)) + hn_bytes + message
                for mon_ws, subs in list(subscriptions.items()):
                    if hostname in subs and monitor_co.get(mon_ws) == company_id:
                        try:
                            await mon_ws.send(prefixed)
                        except Exception:
                            pass
    except Exception:
        pass
    finally:
        agents.pop(hostname, None)
        print(f"[-] Agente desconectado: {hostname}")
        await broadcast_company(company_id, {
            "type": "agent_disconnected", "hostname": hostname,
        })


async def handle_monitor(websocket, hello: dict):
    token = hello.get("token", "")
    try:
        payload    = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        company_id = payload.get("company_id")
        co_name    = payload.get("name", "")
        if not company_id:
            await websocket.close()
            return
    except jwt.ExpiredSignatureError:
        await websocket.send(json.dumps({"type": "error", "detail": "token_expired"}))
        await websocket.close()
        return
    except Exception:
        await websocket.send(json.dumps({"type": "error", "detail": "token_invalid"}))
        await websocket.close()
        return

    monitors.add(websocket)
    subscriptions[websocket] = set()
    monitor_co[websocket]    = company_id
    print(f"[+] Monitor: {co_name} ({company_id})")

    agent_list = [
        {"hostname": h, "info": d["info"]}
        for h, d in agents.items()
        if d["company_id"] == company_id
    ]
    await websocket.send(json.dumps({"type": "agent_list", "agents": agent_list}))

    try:
        async for message in websocket:
            if isinstance(message, str):
                data     = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "subscribe":
                    hn = data.get("hostname")
                    if hn and agents.get(hn, {}).get("company_id") == company_id:
                        subscriptions[websocket].add(hn)
                elif msg_type == "unsubscribe":
                    hn = data.get("hostname")
                    if hn:
                        subscriptions[websocket].discard(hn)
    except Exception:
        pass
    finally:
        monitors.discard(websocket)
        subscriptions.pop(websocket, None)
        monitor_co.pop(websocket, None)
        print(f"[-] Monitor desconectado: {co_name}")


async def broadcast_company(company_id: str, data: dict):
    msg = json.dumps(data)
    for ws in list(monitors):
        if monitor_co.get(ws) == company_id:
            try:
                await ws.send(msg)
            except Exception:
                pass


async def main():
    port = int(os.environ.get("PORT", 8765))
    print(f"Relay v3 multi-tenant iniciando en puerto {port}...")
    async with websockets.serve(handle_connection, "0.0.0.0", port):
        print(f"OK — escuchando en 0.0.0.0:{port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
