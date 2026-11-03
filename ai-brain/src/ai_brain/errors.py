"""Errors shared below both ``tools`` and ``memory``."""

from __future__ import annotations


class BadInput(ValueError):
    """An argument refused before anything was done with it.

    ``ToolRegistry.dispatch`` hands it to the model as a plain error instead of
    logging a tool crash, traceback and all, for what is ordinary bad input. A
    ``ValueError`` so code outside a tool call handles it like any other.
    """
