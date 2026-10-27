import logging

from orb import logs


def test_the_log_rotates_and_never_holds_more_than_its_files(tmp_path):
    path = tmp_path / "server.claude.log"
    handler = logs.setup(path, max_bytes=2_000, backups=2)
    try:
        for i in range(400):
            logging.getLogger("orb").info("line %04d %s", i, "x" * 40)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    files = sorted(tmp_path.glob("server.claude.log*"))
    assert [f.name for f in files] == ["server.claude.log", "server.claude.log.1", "server.claude.log.2"]
    assert all(f.stat().st_size <= 2_100 for f in files)
    assert "line 0399" in path.read_text(encoding="utf-8")


def test_tail_reads_back_through_the_rotated_files_oldest_first(tmp_path):
    path = tmp_path / "server.claude.log"
    path.with_name("server.claude.log.2").write_text("a\nb\n", encoding="utf-8")
    path.with_name("server.claude.log.1").write_text("c\nd\n", encoding="utf-8")
    path.write_text("e\n", encoding="utf-8")
    assert logs.tail(path, 4) == ["b", "c", "d", "e"]
    assert logs.tail(path, 1) == ["e"]


def test_tail_of_no_log_is_empty(tmp_path):
    assert logs.tail(tmp_path / "server.claude.log", 10) == []
