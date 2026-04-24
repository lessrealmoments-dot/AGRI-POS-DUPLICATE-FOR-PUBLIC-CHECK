"""
WebSocket connection manager for AgriSmart Terminal real-time communication.
Handles: pairing notifications, PO assignments, transfer assignments.
"""
from fastapi import WebSocket
from typing import Dict
import json
import asyncio


class TerminalConnectionManager:
    """Manages WebSocket connections for terminals and pairing screens."""

    def __init__(self):
        # Terminals connected by terminal_id
        self.terminal_connections: Dict[str, WebSocket] = {}
        # Pairing screens waiting by code
        self.pairing_connections: Dict[str, WebSocket] = {}

    async def connect_pairing(self, code: str, websocket: WebSocket):
        await websocket.accept()
        self.pairing_connections[code] = websocket

    async def connect_terminal(self, terminal_id: str, websocket: WebSocket):
        await websocket.accept()
        self.terminal_connections[terminal_id] = websocket

    def disconnect_pairing(self, code: str):
        self.pairing_connections.pop(code, None)

    def disconnect_terminal(self, terminal_id: str):
        self.terminal_connections.pop(terminal_id, None)

    async def notify_paired(self, code: str, session_data: dict):
        """Notify the pairing screen that the code has been paired."""
        ws = self.pairing_connections.get(code)
        if ws:
            try:
                await ws.send_json({"type": "paired", "data": session_data})
            except Exception:
                self.disconnect_pairing(code)

    async def notify_terminal(self, terminal_id: str, event_type: str, data: dict):
        """Send a real-time event to a specific terminal."""
        ws = self.terminal_connections.get(terminal_id)
        if ws:
            try:
                await ws.send_json({"type": event_type, "data": data})
            except Exception:
                self.disconnect_terminal(terminal_id)

    async def notify_branch_terminals(self, branch_id: str, event_type: str, data: dict):
        """
        Notify all terminals connected to a specific branch.
        Reserved for future real-time branch-wide push events (e.g. price updates, alerts).
        Not yet called from any live flow — implement alongside the first consumer.
        """
        pass  # intentional stub — no consumers yet

    def get_connected_terminal_ids(self):
        return list(self.terminal_connections.keys())


# Singleton
terminal_ws_manager = TerminalConnectionManager()
