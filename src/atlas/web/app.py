"""Authenticated, read-only local dashboard. No execution or research-control API."""

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from atlas.activity import ActivityEvent, EventType, PositionView
from atlas.config import AtlasError
from atlas.dashboard_models import (
    ChartHistory,
    Envelope,
    HealthView,
    Page,
    ReadModels,
    RiskView,
    RunView,
    Summary,
)
from atlas.dashboard_security import COOKIE, DashboardAuth, DashboardSettings
from atlas.history import HistoryStore

RunID = Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=1024)


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            size += len(message.get("body", b""))
            if size > 8192:
                await JSONResponse({"detail": "Request too large."}, status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        used = False

        async def limited_receive():
            nonlocal used
            if used:
                return await receive()
            used = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, limited_receive, send)


def create_app(store: HistoryStore | None = None, settings: DashboardSettings | None = None):
    store = store if store is not None else HistoryStore()
    settings = settings if settings is not None else DashboardSettings()
    auth, models = DashboardAuth(store), ReadModels(store)
    folder = Path(__file__).parent
    templates = Jinja2Templates(directory=folder / "templates")
    app = FastAPI(
        title="Project Atlas — Synthetic Research", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store, app.state.auth = store, auth
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)
    app.mount("/static", StaticFiles(directory=folder / "static"), name="static")

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin != expected:
                return JSONResponse({"detail": "Same-origin request required."}, status_code=403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'; object-src 'none'",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        if settings.secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(AtlasError)
    async def safe_error(request, error):
        # Local static errors only; never return upstream payloads or exception URLs.
        return JSONResponse(
            {"detail": "Research data unavailable; check dashboard-check locally."}, status_code=503
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "Invalid request."}, status_code=422)

    def authenticated(request: Request):
        if not auth.valid(request.cookies.get(COOKIE)):
            raise HTTPException(401, "Dashboard login required.")

    @app.get("/login")
    def login_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="login.html", context={"configured": auth.configured()}
        )

    @app.post("/auth/login")
    def login(body: Login, request: Request):
        client = request.client.host if request.client else "unknown"
        token = auth.login(body.password.get_secret_value(), client)
        if token is None:
            raise HTTPException(401, "Login unsuccessful or temporarily rate limited.")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE,
            token,
            max_age=8 * 3600,
            httponly=True,
            secure=settings.secure,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/auth/logout", dependencies=[Depends(authenticated)])
    def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/")
    def home(request: Request):
        if not auth.valid(request.cookies.get(COOKIE)):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request=request, name="dashboard.html", context={})

    @app.get("/api/runs", response_model=Page[RunView], dependencies=[Depends(authenticated)])
    def runs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1_000_000)):
        rows, total = store.runs(limit, offset)
        items = [
            RunView(
                run_id=row["run_id"],
                status=row["status"],
                created_at=row["created_at"],
                dataset=row["metadata"].get("dataset", "unknown"),
                venue=row["metadata"].get("venue", "unknown"),
            )
            for row in rows
        ]
        return Page(items=items, total=total, limit=limit, offset=offset)

    @app.get(
        "/api/portfolio", response_model=Envelope[Summary], dependencies=[Depends(authenticated)]
    )
    def portfolio(run_id: RunID = None):
        return models.summary(run_id)

    @app.get(
        "/api/equity", response_model=Envelope[ChartHistory], dependencies=[Depends(authenticated)]
    )
    @app.get(
        "/api/pnl", response_model=Envelope[ChartHistory], dependencies=[Depends(authenticated)]
    )
    @app.get(
        "/api/drawdown",
        response_model=Envelope[ChartHistory],
        dependencies=[Depends(authenticated)],
    )
    def history(
        run_id: RunID = None,
        period: Literal["all", "day", "week", "month"] = "all",
        limit: int = Query(1000, ge=1, le=1000),
    ):
        return models.history(run_id, range_name=period, limit=limit)

    @app.get(
        "/api/positions",
        response_model=Envelope[list[PositionView]],
        dependencies=[Depends(authenticated)],
    )
    def positions(run_id: RunID = None):
        return models.positions(run_id)

    @app.get(
        "/api/trades",
        response_model=Envelope[Page[ActivityEvent]],
        dependencies=[Depends(authenticated)],
    )
    def trades(
        run_id: RunID = None,
        search: str = Query("", max_length=80),
        side: Literal["buy", "sell"] | None = None,
        sort: Literal["timestamp", "instrument", "side"] = "timestamp",
        direction: Literal["asc", "desc"] = "desc",
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
    ):
        return models.trades(
            run_id,
            search=search,
            side=side,
            sort=sort,
            direction=direction,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/trades/{event_id}",
        response_model=ActivityEvent,
        dependencies=[Depends(authenticated)],
    )
    def trade_detail(event_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}", event_id):
            raise HTTPException(404, "Simulated fill not found.")
        with store.connect() as db:
            row = db.execute(
                "SELECT content FROM events WHERE event_id=? AND event_type IN (?,?)",
                (event_id, EventType.FILL.value, EventType.PARTIAL.value),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Simulated fill not found.")
        return json.loads(row[0])

    @app.get("/api/risk", response_model=Envelope[RiskView], dependencies=[Depends(authenticated)])
    def risk(run_id: RunID = None):
        return models.risk(run_id)

    @app.get(
        "/api/activity", response_model=Page[ActivityEvent], dependencies=[Depends(authenticated)]
    )
    def activity(
        run_id: RunID = None,
        kind: EventType | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
    ):
        selected = store.run(run_id)
        if selected is None:
            return Page(items=[], total=0, limit=limit, offset=offset)
        rows, total = store.events(
            selected["run_id"], kinds=(kind,) if kind else (), limit=limit, offset=offset
        )
        return Page(items=rows, total=total, limit=limit, offset=offset)

    @app.get(
        "/api/health", response_model=Envelope[HealthView], dependencies=[Depends(authenticated)]
    )
    def health(run_id: RunID = None):
        return models.health(run_id)

    @app.get(
        "/api/notifications",
        response_model=Envelope[HealthView],
        dependencies=[Depends(authenticated)],
    )
    def notification_status(run_id: RunID = None):
        return models.health(run_id)

    return app
