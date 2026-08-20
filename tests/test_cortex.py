from core.cortex import build_capabilities, select_capability


def _make_declarations():
    tool_declarations = [
        {
            "name": "web_search",
            "description": "Search the web for general information.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
        {
            "name": "news",
            "description": "Get current dated news from RSS feeds.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
    ]
    return tool_declarations, []


def test_build_capabilities_counts():
    tools, plugins = _make_declarations()
    caps = build_capabilities(tools, plugins)
    assert len(caps) == 2


def test_select_news_over_web_search():
    tools, plugins = _make_declarations()
    caps = build_capabilities(tools, plugins)
    decision = select_capability("Give me the latest AI news today", caps)
    assert decision["selected"].name == "news"


def test_select_web_search_for_general_query():
    tools, plugins = _make_declarations()
    caps = build_capabilities(tools, plugins)
    decision = select_capability("Search who created Python", caps)
    assert decision["selected"].name == "web_search"