import os

import uvicorn

from app.config import load_config


def main() -> None:
    load_config()
    uvicorn.run(
        "app.api.app:app", host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")), workers=1,
    )


if __name__ == "__main__":
    main()
