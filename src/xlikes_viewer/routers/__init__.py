"""FastAPI APIRouter modules extracted from the server.create_app god-function.

Each module exposes a ``router`` that reaches shared state via
``Depends(get_context)`` (see xlikes_viewer.context). server.create_app builds
the AppContext and includes these routers.
"""
