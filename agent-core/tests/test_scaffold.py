def test_workspace_importable() -> None:
    import agent_core

    assert agent_core.__doc__ is not None
