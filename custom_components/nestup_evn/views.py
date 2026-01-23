"""HTTP views for EVN integration."""

import logging
import mimetypes
import os
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

_LOGGER = logging.getLogger(__name__)


class EVNPingView(HomeAssistantView):
    """Simple ping endpoint to verify API is working."""

    url = "/api/nestup_evn/ping"
    name = "api:nestup_evn:ping"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the view."""
        self.hass = hass

    async def get(self, request):
        """Handle GET request."""
        return web.json_response({
            "status": "ok",
            "message": "EVN API is running"
        })


class EVNStaticView(HomeAssistantView):
    """Serve static files from webui directory."""

    url = "/evn-monitor/{filename:.*}"
    name = "evn_monitor:static"
    requires_auth = False

    def __init__(self, webui_path: str):
        """Initialize the static file server.
        
        Args:
            webui_path: Absolute path to the webui directory
        """
        self.webui_path = Path(webui_path)
        _LOGGER.info("EVNStaticView initialized with path: %s", self.webui_path)

    async def get(self, request, filename: str):
        """Serve a static file.
        
        Args:
            request: The HTTP request
            filename: Relative path to the file (e.g., "index.html" or "assets/js/main.js")
        """
        # Default to index.html if no filename or directory requested
        if not filename or filename.endswith('/'):
            filename = filename + 'index.html' if filename else 'index.html'

        # Construct full file path
        file_path = self.webui_path / filename
        
        # Security check: ensure the resolved path is within webui_path
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(self.webui_path.resolve())):
                _LOGGER.warning("Attempted path traversal: %s", filename)
                return web.Response(status=403, text="Forbidden")
        except Exception as ex:
            _LOGGER.error("Error resolving path %s: %s", filename, str(ex))
            return web.Response(status=400, text="Bad Request")

        # Check if file exists
        if not file_path.is_file():
            _LOGGER.warning("File not found: %s", file_path)
            return web.Response(status=404, text="Not Found")

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"
            
        # Force UTF-8 for text/* and application/javascript
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"

        # Read and return file
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            return web.Response(
                body=content,
                content_type=content_type,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
        except Exception as ex:
            _LOGGER.error("Error reading file %s: %s", file_path, str(ex))
            return web.Response(status=500, text="Internal Server Error")
