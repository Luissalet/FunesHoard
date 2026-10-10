# Demo data

`--demo` uses a scripted foreground probe and seeds three synthetic days.
It does not discover or poll the person's Windows Recent Items directory.
The seeded file events remain available through the UI, API and MCP.
Normal recording continues to poll Windows Recent Items as before.

This change does not erase an existing data directory. Use a separate data
directory for a demonstration; an existing database may contain previously
recorded activity. Agent and federated queries still use their configured
sources, so a synthetic foreground probe is not a replacement for isolating
the Hub and sibling apps during tests.
