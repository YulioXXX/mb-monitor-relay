# relay/relay_server.py
# Servidor intermediario — corre en Render.com
# Recibe conexiones de agentes y monitores, reenvía frames entre ellos

import asyncio
import websockets
import json
import os

# ─── ESTADO GLOBAL ───────────────────────────────────────────────────────────
# agents  = { hostname: websocket }  — agentes conectados
# monitors = { websocket }           — monitores conectados
# subscriptions = { monitor_ws: hostname } — qué monitor ve qué agente

agents        = {}   # hostname → websocket del agente
agents_info   = {}   # hostname → dict con info del equipo
monitors      = set()
subscriptions = {}   # monitor_ws → hostname que está viendo

# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def handle_connection(websocket):
    """Punto de entrada — el primer mensaje determina si es agente o monitor."""
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
    """Gestiona la conexión de un agente."""
    hostname = hello_data.get("hostname", "unknown")
    info     = hello_data.get("info", {})

    # Registrar agente
    agents[hostname]      = websocket
    agents_info[hostname] = info

    print(f"[+] Agente conectado: {hostname}")

    # Notificar a todos los monitores que hay un agente nuevo
    await broadcast_monitors({
        "type":     "agent_connected",
        "hostname": hostname,
        "info":     info
    })

    try:
        async for message in websocket:
            # El agente envía frames como bytes
            if isinstance(message, bytes):
                # Reenviar el frame solo al monitor que esté viendo este agente
                for monitor_ws, subscribed_hostname in list(subscriptions.items()):
                    if subscribed_hostname == hostname:
                        try:
                            await monitor_ws.send(message)
                        except Exception:
                            pass
            # O mensajes JSON (heartbeat, etc.)
            elif isinstance(message, str):
                pass  # ignorar por ahora
    except Exception:
        pass
    finally:
        agents.pop(hostname, None)
        agents_info.pop(hostname, None)
        print(f"[-] Agente desconectado: {hostname}")
        await broadcast_monitors({
            "type":     "agent_disconnected",
            "hostname": hostname
        })

async def handle_monitor(websocket):
    """Gestiona la conexión del monitor (jefe)."""
    monitors.add(websocket)
    print(f"[+] Monitor conectado")

    # Enviar lista de agentes ya conectados
    agent_list = [
        {"hostname": h, "info": agents_info.get(h, {})}
        for h in agents
    ]
    await websocket.send(json.dumps({
        "type":   "agent_list",
        "agents": agent_list
    }))

    try:
        async for message in websocket:
            if isinstance(message, str):
                data = json.loads(message)
                msg_type = data.get("type")

                # El monitor quiere ver la pantalla de un agente
                if msg_type == "subscribe":
                    hostname = data.get("hostname")
                    subscriptions[websocket] = hostname
                    print(f"[~] Monitor suscrito a: {hostname}")

                # El monitor deja de ver un agente
                elif msg_type == "unsubscribe":
                    subscriptions.pop(websocket, None)
    except Exception:
        pass
    finally:
        monitors.discard(websocket)
        subscriptions.pop(websocket, None)
        print(f"[-] Monitor desconectado")

async def broadcast_monitors(data: dict):
    """Envía un mensaje JSON a todos los monitores conectados."""
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
    print(f"Relay server iniciando en puerto {port}...")
    async with websockets.serve(handle_connection, "0.0.0.0", port):
        print(f"Relay server corriendo en 0.0.0.0:{port}")
        await asyncio.Future()  # correr para siempre

if __name__ == "__main__":
    asyncio.run(main())
