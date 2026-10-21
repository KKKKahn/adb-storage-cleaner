import klean.scan as scan_module
from klean.scan import Entry, load_latest, parse_du, save_scan

DU_TEXT = (
    "4096\t/sdcard/\n"
    "2048\t/sdcard/DCIM\n"
    "1024\t/sdcard/DCIM/a.jpg\n"
    "1024\t/sdcard/DCIM/b.jpg\n"
    "500\t/sdcard/note.txt\n"
    "300\t/sdcard/我的 文件\n"
    "300\t/sdcard/我的 文件/x.bin\n"
)


def test_parse_du_basic():
    scan = parse_du(DU_TEXT, root="/sdcard")
    assert scan.root == "/sdcard"
    assert scan.total_files == 4
    assert scan.total_size == 1024 + 1024 + 500 + 300
    dirs = {entry.path for entry in scan.entries if entry.is_dir}
    assert dirs == {"/sdcard", "/sdcard/DCIM", "/sdcard/我的 文件"}
    assert scan.size_map()["/sdcard/DCIM"] == 2048


def test_parse_du_without_root_line():
    text = "\n".join(line for line in DU_TEXT.splitlines() if not line.endswith("\t/sdcard/"))
    scan = parse_du(text, root="/sdcard")
    assert scan.total_size == 1024 + 1024 + 500 + 300  # root now sums file sizes only
    root_entry = next(entry for entry in scan.entries if entry.path == "/sdcard")
    assert root_entry.size == 1024 + 1024 + 500 + 300


def test_parse_du_ignores_other_paths():
    text = "100\t/sdcard/a\n200\t/storage/emulated/666/b\n300\t/data/local/tmp/c\n"
    scan = parse_du(text, root="/sdcard")
    assert [entry.path for entry in scan.files] == ["/sdcard/a"]


def test_scan_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_module, "state_dir", lambda: tmp_path)
    scan = parse_du(DU_TEXT, root="/sdcard")
    scan.serial = "ABC123"
    save_scan(scan)
    loaded = load_latest("ABC123")
    assert loaded is not None
    assert loaded.serial == "ABC123"
    assert loaded.total_files == scan.total_files
    assert loaded.total_size == scan.total_size
    assert loaded.size_map() == scan.size_map()
    assert isinstance(loaded.entries[0], Entry)
