"""Fraud HTTP routes (docs/M5-DESIGN.md §3.3, §3.3b). The admin review queue, audit view and
member Reviews endpoints arrive with tasks B41, B44 and B45; until then the router is empty."""

from fastapi import APIRouter

router = APIRouter()
