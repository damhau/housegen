"""Headless rendering: the local browser, the remote service and its client.

`renderer` is what the app renders with (`RenderClient`: the service when RENDER_SERVICE_URL
is set, the local browser otherwise).
"""

from housegen.render.remote import RenderClient
from housegen.render.renderer import RenderResult, SceneRenderer

renderer = RenderClient()

__all__ = ["RenderResult", "SceneRenderer", "renderer"]
