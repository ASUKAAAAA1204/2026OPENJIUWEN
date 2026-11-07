"""Two local MCP tools with independently readable, sandboxed write effects."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from mcp.server.fastmcp import FastMCP


def serve(directory: Path):
    server = FastMCP("a-probe")

    @server.tool()
    def update_ticket(ticket: str) -> str:
        """Record the status change for one test ticket."""
        with closing(sqlite3.connect(directory / "tickets.sqlite")) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS updates (ticket TEXT, write_id TEXT)")
            db.execute("INSERT INTO updates VALUES (?, ?)", (ticket, uuid4().hex))
        return "ok"

    @server.tool()
    def append_note(ticket: str) -> str:
        """Append a note for one test ticket."""
        with (directory / "notes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ticket": ticket, "write_id": uuid4().hex}) + "\n")
        return "ok"

    server.run(transport="stdio")


if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve(strict=True))
