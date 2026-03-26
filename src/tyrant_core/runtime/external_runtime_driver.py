"""EXTERNAL runtime driver。"""

from __future__ import annotations

from tyrant_core.models import AttachmentKind

from .endpoint_runtime_driver import EndpointRuntimeDriver


class ExternalRuntimeDriver(EndpointRuntimeDriver):
    """把外部 webhook / connector 接成 WEBHOOK attachment。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            runtime_name="external",
            driver_name="external-runtime",
            config_keys=("external_runtime", "external"),
            attachment_kind=AttachmentKind.WEBHOOK,
            gateway_id="gateway:external-runtime",
            **kwargs,
        )
