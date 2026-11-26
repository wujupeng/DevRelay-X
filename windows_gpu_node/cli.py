import argparse
from pathlib import Path

import uvicorn

from windows_gpu_node.app import create_app
from windows_gpu_node.config import NodeConfig, load_config, load_token
from windows_gpu_node.logging_setup import configure_logging


def _default_config_path() -> Path:
    import os

    configured = os.environ.get("DRX_GPU_NODE_CONFIG")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "DevRelayX" / "WindowsGpuNode" / "config.yaml"
    return Path("config.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows GPU Node Agent")
    parser.add_argument("--config", type=Path, default=_default_config_path())
    arguments = parser.parse_args()

    config: NodeConfig = load_config(arguments.config)
    token = load_token(config)
    if config.tls_cert_file is not None and config.tls_key_file is not None:
        if not config.tls_cert_file.is_file() or not config.tls_key_file.is_file():
            raise SystemExit("Configured TLS certificate or key file does not exist")

    configure_logging(config)
    application = create_app(config, auth_token=token)
    uvicorn.run(
        application,
        host=config.bind_host,
        port=config.port,
        ssl_certfile=str(config.tls_cert_file) if config.tls_cert_file else None,
        ssl_keyfile=str(config.tls_key_file) if config.tls_key_file else None,
        access_log=False,
        log_config=None,
    )
