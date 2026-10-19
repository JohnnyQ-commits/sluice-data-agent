from sluice.cli.main import build_parser


def test_web_subcommand_parses():
    args = build_parser().parse_args(["web", "--port", "5002"])
    assert args.command == "web" and args.port == 5002 and args.host == "127.0.0.1"


def test_web_defaults():
    args = build_parser().parse_args(["web"])
    assert args.command == "web" and args.port == 5001 and args.host == "127.0.0.1"
