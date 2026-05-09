"""Entry point — launches browser + web UI."""

from __future__ import annotations

import asyncio
import logging
import sys
import webbrowser

import uvicorn

from .config import get_settings


def setup_logging() -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quieten noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def launch_browser_background() -> None:
    """Launch the browser in the background so it's ready when the UI loads."""
    from .navigator import launch_browser, get_page, navigate_to_home

    try:
        await launch_browser()
        page = await get_page()
        await navigate_to_home(page)
        logging.getLogger(__name__).info("Browser ready — navigate to TAS and log in.")
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to launch browser: %s", exc)


def main() -> None:
    """Application entry point."""
    setup_logging()
    logger = logging.getLogger(__name__)

    settings = get_settings()
    port = settings.ui_port

    logger.info("=" * 60)
    logger.info("  TAS Auto Completion Assistant")
    logger.info("  Web UI: http://localhost:%d", port)
    logger.info("  Dry Run: %s", settings.dry_run)
    logger.info("  LLM Provider: %s (%s)", settings.llm_provider, settings.llm_model)
    logger.info("=" * 60)

    # Launch browser in background after server starts
    from .ui import app

    @app.on_event("startup")
    async def on_startup():
        asyncio.create_task(launch_browser_background())
        # Open the web UI in the default browser
        webbrowser.open(f"http://localhost:{port}")

    # Run uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
