from fastapi import Header, HTTPException
from app import config


async def verify_api_key(x_api_key: str = Header(...)) -> None:
    """
    Require a valid API key on every request via the X-Api-Key header.
    Set the API_KEY environment variable before starting the server.
    """
    if not config.API_KEY or x_api_key != config.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
