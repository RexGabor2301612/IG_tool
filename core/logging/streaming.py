"""WebSocket streaming for real-time logs."""

import json
import threading
from typing import List, Optional, Callable


class LogStreamBroadcaster:
    """Thread-safe broadcaster for WebSocket clients."""
    
    def __init__(self):
        self.clients: List[dict] = []
        self.lock = threading.RLock()
        self.message_buffer: List[dict] = []
    
    def register_ws(self, ws) -> str:
        """Register WebSocket client.
        
        Args:
            ws: Flask-Sock WebSocket object
            
        Returns:
            Client ID
        """
        import uuid
        client_id = str(uuid.uuid4())
        
        with self.lock:
            self.clients.append({
                "id": client_id,
                "ws": ws,
                "connected": True,
            })
        
        return client_id
    
    def unregister_ws(self, client_id: str):
        """Disconnect WebSocket client."""
        with self.lock:
            self.clients = [c for c in self.clients if c["id"] != client_id]
    
    def broadcast_log_entry(self, log_entry: dict):
        """Send log entry to all connected clients.
        
        Args:
            log_entry: Dict with timestamp, level, action, details
        """
        message = {
            "type": "log",
            "data": log_entry,
        }
        
        self.message_buffer.append(message)
        self._send_to_all_clients(message)
    
    def broadcast_status_update(self, status_data: dict):
        """Send status snapshot to all clients.
        
        Args:
            status_data: Job status dict
        """
        message = {
            "type": "status",
            "data": status_data,
        }
        
        self._send_to_all_clients(message)
    
    def broadcast_progress(self, progress_data: dict):
        """Send progress update to all clients.
        
        Args:
            progress_data: Dict with progress %, current_post, etc.
        """
        message = {
            "type": "progress",
            "data": progress_data,
        }
        
        self._send_to_all_clients(message)
    
    def _send_to_all_clients(self, message: dict):
        """Send message to all connected clients."""
        message_json = json.dumps(message)
        dead_clients = []
        
        with self.lock:
            for client in self.clients:
                try:
                    client["ws"].send(message_json)
                except Exception as e:
                    dead_clients.append(client["id"])
        
        # Clean up dead connections
        for client_id in dead_clients:
            self.unregister_ws(client_id)
    
    def get_clients_count(self) -> int:
        """Get number of connected clients."""
        with self.lock:
            return len(self.clients)
    
    def get_message_history(self, limit: int = 50) -> list:
        """Get recent broadcast messages."""
        return self.message_buffer[-limit:]
