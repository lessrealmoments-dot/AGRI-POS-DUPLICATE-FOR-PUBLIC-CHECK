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
        # branch_id → set of terminal_ids (for branch-wide push)
        self.branch_terminal_map: Dict[str, set] = {}

    async def connect_pairing(self, code: str, websocket: WebSocket):
        await websocket.accept()
        self.pairing_connections[code] = websocket

    async def connect_terminal(self, terminal_id: str, websocket: WebSocket, branch_id: str = ""):
        await websocket.accept()
        self.terminal_connections[terminal_id] = websocket
        if branch_id:
            self.branch_terminal_map.setdefault(branch_id, set()).add(terminal_id)

    def disconnect_pairing(self, code: str):
        self.pairing_connections.pop(code, None)

    def disconnect_terminal(self, terminal_id: str):
        self.terminal_connections.pop(terminal_id, None)
        # Remove from branch map
        for branch_id, tids in list(self.branch_terminal_map.items()):
            tids.discard(terminal_id)
            if not tids:
                del self.branch_terminal_map[branch_id]

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
        Used for branch-wide print job push and price update alerts.
        """
        terminal_ids = list(self.branch_terminal_map.get(branch_id, set()))
        for tid in terminal_ids:
            await self.notify_terminal(tid, event_type, data)

    def get_connected_terminal_ids(self):
        return list(self.terminal_connections.keys())


# Singleton
terminal_ws_manager = TerminalConnectionManager()
