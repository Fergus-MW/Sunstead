"""Local dev entrypoint. Run: `python main.py` or `uvicorn app.main:app --reload`."""

import uvicorn

from app.main import app  # re-export for `uvicorn main:app`

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
