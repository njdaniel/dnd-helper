def test_provider_casing_matches_the_application(monkeypatch, capsys) -> None:
    """Preflight must reject what Settings rejects.

    Settings.llm_provider is a case-sensitive Literal, so a preflight that
    case-folds would report success on a config the bot refuses to start with.
    """
    import scripts.preflight as preflight

    monkeypatch.setattr(preflight, "load_config", lambda: {"LLM_PROVIDER": "OLLAMA"})
    assert preflight.main() == 1
    out = capsys.readouterr().out
    assert "unsupported LLM_PROVIDER" in out
    assert "lowercase it" in out  # names the actual fix
