"""MCP server exposing the Nimbus ontology to agents. Run: python mcp_server.py (stdio)."""
from mcp.server.mcpserver import MCPServer

import ontology

mcp = MCPServer("nimbus-ontology")


@mcp.tool()
def list_exception_types() -> dict:
    """List exception types with their label and family."""
    return {"version": ontology.ONTOLOGY_VERSION,
            "types": {k: {"label": v["label"], "family": v["family"]} for k, v in ontology.EXCEPTION_TYPES.items()}}


@mcp.tool()
def get_exception_type(exception_type: str) -> dict:
    """Full definition: evidence requirements, known causes, disposition and exposure basis."""
    od = ontology.get(exception_type)
    if not od:
        return {"error": f"unknown exception type {exception_type}"}
    return {"version": ontology.ONTOLOGY_VERSION, "type": exception_type, **od}


@mcp.tool()
def get_evidence_requirements(exception_type: str) -> list[str]:
    """Evidence an investigation must obtain for this exception type."""
    od = ontology.get(exception_type)
    return od["evidence"] if od else ontology.DEFAULT_EVIDENCE


if __name__ == "__main__":
    mcp.run()
