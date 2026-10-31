"""Shared task groups for Manager pages and command discovery."""

GROUPS = (
    ("Monitor", ("status", "checks", "images"), ("status", "attackers", "check", "images", "ps")),
    ("Configure", ("edition", "llm", "settings"), ("customize", "edition", "env", "llm")),
    ("Access", ("sensors", "users"), ("sensors", "users")),
    ("Data & maintenance", ("elastic", "update"),
     ("elastic", "install", "restart", "restore", "setup", "start", "stop", "uninstall", "update")),
)

PAGE_ORDER = tuple(page for _, pages, _ in GROUPS for page in pages)
PAGE_HEADINGS = {pages[0]: title for title, pages, _ in GROUPS}
PAGE_GROUPS = {page: title for title, pages, _ in GROUPS for page in pages}
