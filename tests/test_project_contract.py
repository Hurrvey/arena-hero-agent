from pathlib import Path

import app


def test_supported_python_and_product_assets_are_declared() -> None:
    root = Path(__file__).parents[1]

    assert app.__version__ == "1.1.0"
    assert (root / "arena-hero-ui-assets" / "ASSET-MANIFEST.json").is_file()
    assert (root / "MVP开发指导.md").is_file()
    assert "ARENA_HERO_ADAPTIVE_AUTO_APPLY=0" in (root / ".env.example").read_text(encoding="utf-8")
