"""Shared FastMCP instance. Tool modules register against this; server.py runs it."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="avocado-tools")
