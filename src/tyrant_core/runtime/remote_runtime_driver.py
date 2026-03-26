"""REMOTE runtime driver。"""

from __future__ import annotations

from tyrant_core.models import AttachmentKind

from .endpoint_runtime_driver import EndpointRuntimeDriver


class RemoteRuntimeDriver(EndpointRuntimeDriver):
    """把远端 agent / websocket consumer 接成 CONNECTOR attachment。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            runtime_name="remote",
            driver_name="remote-runtime",
            config_keys=("remote_runtime", "remote"),
            attachment_kind=AttachmentKind.CONNECTOR,
            gateway_id="gateway:remote-runtime",
            **kwargs,
        )
