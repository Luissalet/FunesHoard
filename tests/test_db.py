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
