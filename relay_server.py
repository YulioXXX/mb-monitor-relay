# relay/relay_server.py
# Servidor intermediario — corre en Render.com
# v2: soporta múltiples suscripciones por monitor + prefija hostname en frames

import asyncio
import websockets
import json
import os
import struct

# ─── ESTADO GLOBAL ───────────────────────────────────────────────────────────
agents        = {}   # hostname → websocket del agente
agents_info   = {}   # hostname → dict con info del equipo
monitors      = set()
# CAMBIO v2: subscriptions ahora es { monitor_ws → set(hostnames) }
subscriptions = {}   # monitor_ws → set de hostnames suscritos

# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def handle_connection(websocket):
    try:
        first_msg = await asyncio.wait_for(websocket.recv(), timeout=15)
        data = json.loads(first_msg)
        role = data.get("role")
        if role == "agent":
            await handle_agent(websocket, data)
        elif role == "monitor":
            await handle_monitor(websocket)
        else:
            await websocket.close()
    except Exception:
        pass


async def handle_agent(websocket, hello_data: dict):
    hostname = hello_data.get("hostname", "unknown")
    info     = hello_data.get("info", {})

    agents[hostname]      = websocket
    agents_info[hostname] = info
    print(f"[+] Agente conectado: {hostname}")

    await broadcast_monitors({
        "type":     "agent_connected",
        "hostname": hostname,
        "info":     info,
    })

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # CAMBIO v2: prefijar el hostname antes de reenviar
                # Formato: [2B: len_hostname][hostname_bytes][jpeg_bytes]
                hostname_bytes = hostname.encode("utf-8")
                prefixed = struct.pack(">H", len(hostname_bytes)) + hostname_bytes + message

                for monitor_ws, subscribed_set in list(subscriptions.items()):
                    if hostname in subscribed_set:
                        try:
                            await monitor_ws.send(prefixed)
                        except Exception:
                            pass
            # Mensajes de texto del agente (ignorados por ahora)
    except Exception:
        pass
    finally:
        agents.pop(hostname, None)
        agents_info.pop(hostname, None)
        print(f"[-] Agente desconectado: {hostname}")
        await broadcast_monitors({
            "type":     "agent_disconnected",
            "hostname": hostname,
        })


async def handle_monitor(websocket):
    monitors.add(websocket)
    subscriptions[websocket] = set()   # CAMBIO v2: empezar con set vacío
    print(f"[+] Monitor conectado")

    # Enviar lista de agentes ya conectados
    agent_list = [
        {"hostname": h, "info": agents_info.get(h, {})}
        for h in agents
    ]
    await websocket.send(json.dumps({
        "type":   "agent_list",
        "agents": agent_list,
    }))

    try:
        async for message in websocket:
            if isinstance(message, str):
                data     = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "subscribe":
                    # CAMBIO v2: agregar al set (no reemplazar)
                    hostname = data.get("hostname")
                    if hostname:
                        subscriptions[websocket].add(hostname)
                        print(f"[~] Monitor suscrito a: {hostname}")

                elif msg_type == "unsubscribe":
                    # CAMBIO v2: quitar del set
                    hostname = data.get("hostname")
                    if hostname:
                        subscriptions[websocket].discard(hostname)
                        print(f"[~] Monitor desuscrito de: {hostname}")

    except Exception:
        pass
    finally:
        monitors.discard(websocket)
        subscriptions.pop(websocket, None)
        print(f"[-] Monitor desconectado")


async def broadcast_monitors(data: dict):
    if not monitors:
        return
    msg = json.dumps(data)
    for ws in list(monitors):
        try:
            await ws.send(msg)
        except Exception:
            pass

# ─── MAIN ────────────────────────────────────────────────────────────────────

async def main():
    port = int(os.environ.get("PORT", 8765))
    print(f"Relay server v2 iniciando en puerto {port}...")
    async with websockets.serve(handle_connection, "0.0.0.0", port):
        print(f"Relay server corriendo en 0.0.0.0:{port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
