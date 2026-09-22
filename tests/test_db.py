from funes_hoard.db import Database


def test_fresh_database_seeds_current_privacy_defaults(tmp_path):
    db = Database(tmp_path / "data")
    patterns = {r["pattern"] for r in db.query("SELECT pattern FROM privacy_rules")}
    assert r"(?i)inc[oó]gnito" in patterns
    assert db.get_meta("privacy_defaults_version", "1") == "2"


def test_reopening_an_old_database_migrates_its_default_privacy_rules(tmp_path):
    data_dir = tmp_path / "data"
    db = Database(data_dir)
    # Simulate a database created before the case/accent fix: the old
    # pattern text, and no version meta recorded yet.
    db.execute("UPDATE privacy_rules SET pattern = 'Incognito' WHERE pattern = '(?i)Incognito'")
    db.execute("DELETE FROM meta WHERE key = 'privacy_defaults_version'")
    db.close()

    reopened = Database(data_dir)
    patterns = {r["pattern"] for r in reopened.query("SELECT pattern FROM privacy_rules")}
    assert "Incognito" not in patterns
    assert "(?i)Incognito" in patterns
    assert r"(?i)inc[oó]gnito" in patterns  # the wholly-new default was added too
    assert reopened.get_meta("privacy_defaults_version", "1") == "2"


def test_reopening_an_old_database_migrates_its_default_classify_rules(tmp_path):
    data_dir = tmp_path / "data"
    db = Database(data_dir)
    db.execute("DELETE FROM meta WHERE key = 'classify_defaults_version'")
    db.close()

    reopened = Database(data_dir)
    patterns = {r["pattern"] for r in reopened.query("SELECT pattern FROM classify_rules")}
    assert "Faustus.exe" in patterns
    assert "Microsoft.Photos.exe" in patterns
    from funes_hoard.core.classify import CLASSIFY_DEFAULTS_VERSION

    assert reopened.get_meta("classify_defaults_version", "1") == str(CLASSIFY_DEFAULTS_VERSION)


def test_classify_migration_never_touches_a_users_own_rule_for_the_same_app(tmp_path):
    data_dir = tmp_path / "data"
    db = Database(data_dir)
    # Simulate a database from before the Faustus.exe default existed, where
    # the user had already mapped it to their own category.
    db.execute("DELETE FROM classify_rules WHERE pattern = 'Faustus.exe'")
    db.execute(
        "INSERT INTO classify_rules(order_idx, match_type, pattern, category, project, enabled)"
        " VALUES (999, 'app', 'Faustus.exe', 'Writing', NULL, 1)"
    )
    db.execute("DELETE FROM meta WHERE key = 'classify_defaults_version'")
    db.close()

    reopened = Database(data_dir)
    rows = reopened.query("SELECT category FROM classify_rules WHERE pattern = 'Faustus.exe'")
    assert [r["category"] for r in rows] == ["Writing"]  # the user's own mapping, not overwritten or duplicated


def test_migration_never_touches_a_users_own_custom_rule(tmp_path):
    data_dir = tmp_path / "data"
    db = Database(data_dir)
    db.execute(
        "INSERT INTO privacy_rules(kind, match_type, pattern, enabled) VALUES ('exclude', 'title_regex', 'MyCustomApp', 1)"
    )
    db.execute("DELETE FROM meta WHERE key = 'privacy_defaults_version'")
    db.close()

    reopened = Database(data_dir)
    patterns = {r["pattern"] for r in reopened.query("SELECT pattern FROM privacy_rules")}
    assert "MyCustomApp" in patterns


def test_classify_migration_puts_the_streaming_rule_before_the_browsers(tmp_path):
    # Rules are first-match: appended after chrome.exe, a "- YouTube" title
    # rule would never fire, and the fan films would stay Browsing (with the
    # 2-minute away threshold) on every existing database.
    from funes_hoard.core.classify import STREAMING_TITLE_RE

    data_dir = tmp_path / "data"
    db = Database(data_dir)
    db.execute("DELETE FROM classify_rules WHERE pattern = ?", (STREAMING_TITLE_RE,))
    db.execute("UPDATE meta SET value = '2' WHERE key = 'classify_defaults_version'")
    db.close()

    reopened = Database(data_dir)
    order = [r["pattern"] for r in reopened.query("SELECT pattern FROM classify_rules ORDER BY order_idx")]
    assert order.count(STREAMING_TITLE_RE) == 1
    assert order.index(STREAMING_TITLE_RE) == order.index("chrome.exe") - 1
    idx = [r["order_idx"] for r in reopened.query("SELECT order_idx FROM classify_rules")]
    assert len(idx) == len(set(idx))  # no two rules share a position
