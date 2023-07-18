from textwrap import dedent

import pytest

from juniorguru.cli.data import (get_row_updates, is_cache, make_schema_idempotent,
                                 merge_unique_lines)


def test_make_schema_idempotent():
    assert make_schema_idempotent(dedent('''
        CREATE TABLE "clubdocumentedrole" ("id" INTEGER NOT NULL PRIMARY KEY, "name" VARCHAR(255) NOT NULL, "mention" VARCHAR(255) NOT NULL, "slug" VARCHAR(255) NOT NULL, "description" TEXT NOT NULL, "position" INTEGER NOT NULL, "emoji" VARCHAR(255));
        CREATE TABLE "clubpinreaction" ("id" INTEGER NOT NULL PRIMARY KEY, "user_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL, FOREIGN KEY ("user_id") REFERENCES "clubuser" ("id"), FOREIGN KEY ("message_id") REFERENCES "clubmessage" ("id"));
        CREATE INDEX "scrapedjob_last_seen_on" ON "scrapedjob" ("last_seen_on");
        CREATE UNIQUE INDEX "scrapedjob_url" ON "scrapedjob" ("url");
    ''')).strip() == dedent('''
        CREATE TABLE IF NOT EXISTS "clubdocumentedrole" ("id" INTEGER NOT NULL PRIMARY KEY, "name" VARCHAR(255) NOT NULL, "mention" VARCHAR(255) NOT NULL, "slug" VARCHAR(255) NOT NULL, "description" TEXT NOT NULL, "position" INTEGER NOT NULL, "emoji" VARCHAR(255));
        CREATE TABLE IF NOT EXISTS "clubpinreaction" ("id" INTEGER NOT NULL PRIMARY KEY, "user_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL, FOREIGN KEY ("user_id") REFERENCES "clubuser" ("id"), FOREIGN KEY ("message_id") REFERENCES "clubmessage" ("id"));
        CREATE INDEX IF NOT EXISTS "scrapedjob_last_seen_on" ON "scrapedjob" ("last_seen_on");
        CREATE UNIQUE INDEX IF NOT EXISTS "scrapedjob_url" ON "scrapedjob" ("url");
    ''').strip()


def test_make_schema_idempotent_raises():
    with pytest.raises(ValueError):
        make_schema_idempotent(dedent('''
            CREATE UNIQUE INDEX "scrapedjob_url" ON "scrapedjob" ("url");
            INSERT INTO "transaction" VALUES(175,'2021-09-01','donations',6);
        '''))


@pytest.mark.parametrize('row_from, row_to, expected', [
    (dict(), dict(), dict()),
    (dict(a=1, b=2, c=3), dict(a=1, b=2, c=3), dict()),
    (dict(a=None, b=None, c=3), dict(a=None, b=None, c=3), dict()),
    (dict(a=1, b=2, c=3), dict(a=1, b=None, c=3), dict(b=2)),
    (dict(a=None, b=None, c=3), dict(a=1, b=2, c=3), dict()),
    (dict(a=None, b=42, c=3, d=None), dict(a=1, b=None, c=3, d=None), dict(b=42)),
])
def test_get_row_updates(row_from, row_to, expected):
    assert get_row_updates(row_from, row_to) == expected


@pytest.mark.parametrize('row_from, row_to', [
    (dict(a=1, b=2, c=3), dict(a=1, b=42, c=3)),
])
def test_get_row_updates_raises_conflict(row_from, row_to):
    with pytest.raises(RuntimeError):
        get_row_updates(row_from, row_to)


@pytest.mark.parametrize('row_from, row_to', [
    (dict(a=1, b=2, c=3, d=4), dict(a=1, b=2, c=3)),
    (dict(a=1, b=2, c=3), dict(a=1, b=2, c=3, d=4)),
])
def test_get_row_updates_raises_inconsistence(row_from, row_to):
    with pytest.raises(ValueError):
        get_row_updates(row_from, row_to)


def test_merge_unique_lines(tmp_path):
    path_from = tmp_path / 'path_from.txt'
    path_from.write_text('a\nb\nc\n')
    path_to = tmp_path / 'path_to.txt'
    path_to.write_text('d\ne\na\nf\nb\n')
    merge_unique_lines(path_from, path_to)

    assert path_from.read_text() == 'a\nb\nc\n'
    assert path_to.read_text() == 'd\ne\na\nf\nb\nc\n'


@pytest.mark.parametrize('path, expected', [
    ('persist-to-workspace/0/.scrapy/http_cache/remoteok/6b/6b0ef6dce04d86506412fbe8e08f417f108f8115/request_headers', True),
    ('persist-to-workspace/0/.sync_cache/memberful-1ff3b5951b15bafd73543a9a6ffc24bf7f0ebb78faa0a0f2c8733fbaaf2d7424.json', True),
    ('persist-to-workspace/0/juniorguru/images/avatars-club/0b5f22a77c1f5510921c930b6cb8ccdb.png', False),
])
def test_is_cache(path, expected):
    assert is_cache(path) is expected
