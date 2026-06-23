import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from theflow.settings import settings as flowsettings

KH_APP_DATA_DIR = getattr(flowsettings, "KH_APP_DATA_DIR", ".")
GRADIO_TEMP_DIR = os.getenv("GRADIO_TEMP_DIR", None)
# override GRADIO_TEMP_DIR if it's not set
if GRADIO_TEMP_DIR is None:
    GRADIO_TEMP_DIR = os.path.join(KH_APP_DATA_DIR, "gradio_tmp")
    os.environ["GRADIO_TEMP_DIR"] = GRADIO_TEMP_DIR


from ktem.main import App  # noqa
from ktem.react_api import register_react_api  # noqa

runtime = App()
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

    host = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
    port = int(os.getenv("GRADIO_SERVER_PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
