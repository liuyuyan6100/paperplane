"""Only the preview's close preference is exposed here."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    close_behavior: Literal["ask", "tray", "exit"]


def preference_router(preferences):
    router = APIRouter(prefix="/api/v2")

    @router.get("/preferences")
    def get():
        return preferences.get()

    @router.post("/preferences")
    def save(values: PreferenceInput):
        return preferences.update(values.close_behavior)

    return router
