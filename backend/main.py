"""MediAssist backend - start with:  python main.py

One process runs everything:
  - FastAPI server (dashboard API)  -> http://127.0.0.1:8000/docs
  - Telegram bot (polling)
  - Reminder scheduler (every few minutes)
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import db
import telegram_bot
from api import router
from public_api import router as public_router
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")


def check_production_secrets():
    """On a server (HOST is not this PC) refuse to start with the default / weak dashboard password or secret."""
    if settings.HOST in ("127.0.0.1", "localhost"):
        return
    problems = []
    if settings.ADMIN_PASSWORD in ("", "change-me") or len(settings.ADMIN_PASSWORD) < 10:
        problems.append("ADMIN_PASSWORD must be at least 10 characters (and not change-me)")
    if settings.JWT_SECRET in ("", "please-change-this-secret", "put-any-long-random-text-here") \
            or len(settings.JWT_SECRET) < 32:
        problems.append("JWT_SECRET must be at least 32 random characters")
    if problems:
        raise RuntimeError("Unsafe settings for a public server: " + "; ".join(problems))


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_production_secrets()
    await db.init()
    await db.database.command("ping")
    log.info("MongoDB connected (%s)", settings.MONGODB_DB)

    app.state.engines = {}
    tg = telegram_bot.build(app.state.engines)
    await tg.initialize()
    await tg.start()
    await tg.updater.start_polling(drop_pending_updates=True)
    me = await tg.bot.get_me()
    app.state.bot_username = me.username  # used by the website's "Book on Telegram" buttons
    log.info("Telegram bot @%s is running", me.username)
    try:
        yield
    finally:
        await tg.updater.stop()
        await tg.stop()
        await tg.shutdown()
        await db.client.close()


app = FastAPI(title="MediAssist API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(public_router)


@app.get("/health")
async def health():
    return {"ok": True}


# Staff dashboard (React build) -> http://127.0.0.1:8000/staff/
# Public clinic website        -> http://127.0.0.1:8000/
ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIST = ROOT / "dashboard" / "dist"
WEBSITE_DIR = ROOT / "website"


def mount_frontends(app):
    from fastapi.responses import RedirectResponse

    @app.middleware("http")
    async def no_stale_pages(request, call_next):
        """Browsers must re-check the website and dashboard pages on every visit, so an update shows
        straight away (hashed dashboard assets under /staff/assets can still be cached)."""
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/api") and not path.startswith("/staff/assets/"):
            response.headers["Cache-Control"] = "no-cache"
        # basic browser protections (no framing of the site, no type sniffing, no full URLs leaked to other sites)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.get("/staff", include_in_schema=False)
    async def staff_redirect():
        return RedirectResponse("/staff/")

    if DASHBOARD_DIST.exists():
        app.mount("/staff", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    else:
        log.warning("Dashboard build not found at %s", DASHBOARD_DIST)
    if WEBSITE_DIR.exists():
        app.mount("/", StaticFiles(directory=WEBSITE_DIR, html=True), name="website")
    else:
        log.warning("Website folder not found at %s", WEBSITE_DIR)


mount_frontends(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
