"""agents-cli / ADK agent package for the Antigravity A2A/A2UI control plane.

This is the ``agent_directory`` declared in ``agents-cli-manifest.yaml``. The
real ADK objects live in :mod:`app.agent`; that module is import-guarded so it
loads even when ``google-adk`` is not installed (the build/test environment here
has no Google SDKs).
"""
