from klean.cli import build_parser


def test_global_serial_before_and_after_command():
    parser = build_parser()
    assert parser.parse_args(["--serial", "ABC", "scan"]).serial == "ABC"
    assert parser.parse_args(["scan", "--serial", "ABC"]).serial == "ABC"


def test_defaults_survive_subparser():
    parser = build_parser()
    args = parser.parse_args(["junk"])
    assert args.apply is False
    assert args.yes is False
    assert not hasattr(args, "serial") or args.serial is None


def test_dupes_options():
    parser = build_parser()
    args = parser.parse_args(["dupes", "--min-size", "500K", "--apply", "--include-app-data"])
    assert args.min_size == "500K"
    assert args.apply is True
    assert args.include_app_data is True


def test_live_options():
    parser = build_parser()
    args = parser.parse_args(["live", "--delete", "--backup", "/tmp/b", "--max-duration", "4000"])
    assert args.delete is True
    assert args.backup == "/tmp/b"
    assert args.max_duration == 4000
    assert args.include_unknown is False


def test_report_cached():
    parser = build_parser()
    args = parser.parse_args(["report", "--cached", "--no-html"])
    assert args.cached is True
    assert args.no_html is True
