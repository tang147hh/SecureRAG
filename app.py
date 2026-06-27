import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from theflow.settings import settings as flowsettings

KH_APP_DATA_DIR = getattr(flowsettings, "KH_APP_DATA_DIR", ".")
APP_TEMP_DIR = os.getenv("APP_TEMP_DIR", None)
if APP_TEMP_DIR is None:
    APP_TEMP_DIR = os.path.join(KH_APP_DATA_DIR, "tmp")
    os.environ["APP_TEMP_DIR"] = APP_TEMP_DIR


from ktem.react_api import register_react_api  # noqa
from ktem.react_runtime import ReactRuntime  # noqa

runtime = ReactRuntime()
runtime.make()

app = FastAPI()
register_react_api(app, runtime)

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


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
