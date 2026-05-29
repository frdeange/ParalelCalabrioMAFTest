"""Integration tests for the MCP server (issue #23).

These tests bootstrap a real SQL Server 2022 instance via
``testcontainers[mssql]`` and exercise the schema + query tools
end-to-end against it. They are opt-in: the top-level
``tests/conftest.py`` auto-skips anything carrying the
``@pytest.mark.integration`` marker unless ``MCP_RUN_INTEGRATION=1``
is set in the environment.
"""
