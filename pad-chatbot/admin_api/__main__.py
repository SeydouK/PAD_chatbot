from .app import app
import os
try:
    from waitress import serve
    serve(app, host=os.getenv("PAD_ADMIN_HOST", "127.0.0.1"), port=int(os.getenv("PAD_ADMIN_PORT", "8010")))
except ImportError:
    app.run(host=os.getenv("PAD_ADMIN_HOST", "127.0.0.1"), port=int(os.getenv("PAD_ADMIN_PORT", "8010")))
