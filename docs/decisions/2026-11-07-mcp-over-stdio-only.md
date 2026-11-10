# MCP over stdio only

- Decided: 0.3.0 (2026-11-07). Recorded: 2026-11-09.
- Status: in force.

## Context

The MCP server lets an agent host call the commands as tools. Its caller is the author of the
text under grade: an agent under optimisation pressure, not a trusted party. A network
transport would add a listening socket, authentication, and callers other than the host that
started the server.

## Decision

`sealed-panel mcp` serves one host over stdio, and nothing else. The host starts the server as a
child process; the server reads its settings once at startup and serves that host until stdin
closes. There is no HTTP transport and no authentication, because there is no one else to
authenticate.

## Consequences

- The server's reach is the user who started it: no port to expose, no token to leak.
- A remote or shared deployment is not supported. One would need its own decision record, with
  the authentication and the threat model that come with it.
- `SECURITY.md` states the stdio-only scope.
