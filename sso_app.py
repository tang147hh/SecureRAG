import os

from authlib.integrations.starlette_client import OAuth, OAuthError
from decouple import config
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.config import Config
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from theflow.settings import settings as flowsettings

KH_APP_DATA_DIR = getattr(flowsettings, "KH_APP_DATA_DIR", ".")
APP_TEMP_DIR = os.getenv("APP_TEMP_DIR", None)
AUTHENTICATION_METHOD = config("AUTHENTICATION_METHOD", "GOOGLE")

if APP_TEMP_DIR is None:
    APP_TEMP_DIR = os.path.join(KH_APP_DATA_DIR, "tmp")
    os.environ["APP_TEMP_DIR"] = APP_TEMP_DIR

# for authentication with Google
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = config("GOOGLE_CLIENT_SECRET", default="")

# for authentication with Open ID by keycloak
KEYCLOAK_SERVER_URL = config("KEYCLOAK_SERVER_URL", default="")
KEYCLOAK_REALM = config("KEYCLOAK_REALM", default="")
KEYCLOAK_CLIENT_ID = config("KEYCLOAK_CLIENT_ID", default="")
KEYCLOAK_CLIENT_SECRET = config("KEYCLOAK_CLIENT_SECRET", default="")
SECRET_KEY = config("SECRET_KEY", default="default-secret-key")


def add_session_middleware(app):
    config_data = {
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
        "KEYCLOAK_CLIENT_ID": KEYCLOAK_CLIENT_ID,
        "KEYCLOAK_CLIENT_SECRET": KEYCLOAK_CLIENT_SECRET,
    }
    starlette_config = Config(environ=config_data)
    oauth = OAuth(starlette_config)

    if AUTHENTICATION_METHOD == "KEYCLOAK":
        oauth.register(
            name="keycloak",
            server_metadata_url=(
                f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/"
                ".well-known/openid-configuration"
            ),
            client_id=KEYCLOAK_CLIENT_ID,
            client_secret=KEYCLOAK_CLIENT_SECRET,
            client_kwargs={
                "scope": "openid email profile",
            },
        )
    else:
        oauth.register(
            name="google",
            server_metadata_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            client_kwargs={
                "scope": "openid email profile",
            },
        )

    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
    return oauth

from ktem.react_api import register_react_api  # noqa
from ktem.react_runtime import ReactRuntime  # noqa

runtime = ReactRuntime()
runtime.make()

app = FastAPI()
register_react_api(app, runtime)
oauth = add_session_middleware(app)
FRONTEND_DIST_DIR = Path(__file__).resolve().parent / "frontend" / "dist"
if FRONTEND_DIST_DIR.exists():
    app.mount(
        "/app",
        StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True),
        name="react-frontend",
    )


@app.get("/")
def root():
    return RedirectResponse(url="/app/")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(runtime._favicon)


@app.route("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")


@app.route("/login")
async def login(request: Request):
    provider = "keycloak" if AUTHENTICATION_METHOD == "KEYCLOAK" else "google"
    redirect_uri = str(request.url_for("auth"))
    return await getattr(oauth, provider).authorize_redirect(request, redirect_uri)


@app.route("/auth")
async def auth(request: Request):
    provider = "keycloak" if AUTHENTICATION_METHOD == "KEYCLOAK" else "google"
    try:
        access_token = await getattr(oauth, provider).authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(url="/")
    request.session["user"] = dict(access_token)["userinfo"]
    return RedirectResponse(url="/")
