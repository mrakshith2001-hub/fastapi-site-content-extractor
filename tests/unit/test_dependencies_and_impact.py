from app.db.models import Operation
from app.services.content import build_content, content_hash
from app.services.dependencies import html_dependencies, resolve, static_dependencies
from app.services.impact import PathChange, plan_reprocessing


def test_resolve_relative_paths():
    assert resolve("info/contact.html", "../css/main.css") == "css/main.css"
    assert resolve("index.html", "./images/a.png?v=2#x") == "images/a.png"
    assert resolve("info/contact.html", "/js/site.js") == "js/site.js"
    assert resolve("index.html", "https://cdn.example.com/x.js") is None
    assert resolve("index.html", "data:image/png;base64,AAA") is None
    assert resolve("index.html", "../../etc/passwd") is None


def test_html_dependencies_cover_common_references():
    html = """
    <link rel="stylesheet" href="css/a.css"><link rel="canonical" href="other.html">
    <script src="js/a.js"></script><img src="img/a.png" srcset="img/a@2x.png 2x, img/a@3x.png 3x">
    <div style="background: url('img/bg.svg')"></div><style>.x{background:url(img/inline.png)}</style>
    <a href="next.html">link only</a>
    """
    assert html_dependencies("index.html", html) == {
        "css/a.css", "js/a.js", "img/a.png", "img/a@2x.png", "img/a@3x.png", "img/bg.svg", "img/inline.png"
    }


def test_static_dependencies_follow_css_imports():
    files = {
        "css/main.css": '@import url("typography.css"); body { background: url("../img/bg.svg"); }',
        "css/typography.css": '@font-face { src: url("../fonts/x.ttf"); }',
    }
    deps = static_dependencies("index.html", '<link rel="stylesheet" href="css/main.css">', files.get)
    assert deps == {"css/main.css", "css/typography.css", "img/bg.svg", "fonts/x.ttf"}


GRAPH = {
    "index.html": {"css/main.css", "js/site.js", "data/trails.json"},
    "trails.html": {"css/main.css", "css/trails.css", "data/trails.json"},
    "gear.html": {"css/main.css", "js/site.js"},
}


def test_page_specific_css_change_only_affects_that_page():
    plan = plan_reprocessing([PathChange("css/trails.css", Operation.UPDATED)], GRAPH)
    assert list(plan.to_render) == ["trails.html"]


def test_shared_data_change_affects_its_consumers_only():
    plan = plan_reprocessing([PathChange("data/trails.json", Operation.UPDATED)], GRAPH)
    assert set(plan.to_render) == {"index.html", "trails.html"}


def test_html_change_affects_only_itself():
    plan = plan_reprocessing([PathChange("gear.html", Operation.UPDATED)], GRAPH)
    assert plan.to_render == {"gear.html": ["page updated"]}


def test_deleted_page_is_retired_not_rendered():
    plan = plan_reprocessing([PathChange("gear.html", Operation.DELETED)], GRAPH)
    assert plan.to_render == {} and plan.to_retire == ["gear.html"]


def test_unreferenced_asset_triggers_nothing():
    plan = plan_reprocessing([PathChange("images/unused.png", Operation.CREATED)], GRAPH)
    assert plan.to_render == {} and plan.to_retire == []


def test_content_formatting_and_hash_stability():
    blocks = [{"type": "heading", "level": 1, "text": "Title"}, {"type": "item", "text": "One"}]
    popup = [{"type": "heading", "level": 2, "text": "Policy"}, {"type": "text", "text": "Details"}]
    content = build_content(blocks, [popup])
    assert content == "# Title\n- One\n\n## [Popup] Policy\nDetails"
    assert content_hash("T", content) == content_hash("T", content)
    assert content_hash("T", content) != content_hash("T2", content)
