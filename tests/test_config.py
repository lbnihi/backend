from app.config import to_async_url


def test_easypanel_url_with_sslmode():
    url = "postgres://qalbalkhalij:pw@qalbalkhalij_database:5432/qalbalkhalij?sslmode=disable"
    assert to_async_url(url) == "postgresql+asyncpg://qalbalkhalij:pw@qalbalkhalij_database:5432/qalbalkhalij?ssl=disable"


def test_plain_and_sqlite_urls_untouched_except_driver():
    assert to_async_url("postgresql://u:p@h:5432/d") == "postgresql+asyncpg://u:p@h:5432/d"
    assert to_async_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
