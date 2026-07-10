from console.secrets.dotenv import parse_dotenv, render_dotenv


def test_basic_lines_parse():
    pairs, skipped = parse_dotenv("DATABASE_URL=postgres://x\nLOG_LEVEL=info\n")
    assert pairs == {"DATABASE_URL": "postgres://x", "LOG_LEVEL": "info"}
    assert skipped == []


def test_comments_blanks_and_export_prefix():
    text = """
# comment line
export API_KEY=abc123

TOKEN=xyz
"""
    pairs, skipped = parse_dotenv(text)
    assert pairs == {"API_KEY": "abc123", "TOKEN": "xyz"}
    assert skipped == []


def test_quoted_values_lose_quotes():
    pairs, _ = parse_dotenv(
        "A=\"with spaces and #hash\"\nB='single # quoted'\nC=\"\"\nD=x\n"
    )
    assert pairs["A"] == "with spaces and #hash"
    assert pairs["B"] == "single # quoted"
    assert pairs["D"] == "x"
    assert "C" not in pairs  # empty after unquoting


def test_unquoted_trailing_comment_stripped():
    pairs, _ = parse_dotenv("PORT_HINT=8080 # the app port\n")
    assert pairs == {"PORT_HINT": "8080"}


def test_value_may_contain_equals():
    pairs, _ = parse_dotenv("QUERY=a=b&c=d\n")
    assert pairs == {"QUERY": "a=b&c=d"}


def test_repeated_key_last_wins():
    pairs, _ = parse_dotenv("A=first\nA=second\n")
    assert pairs == {"A": "second"}


def test_bad_lines_reported_with_line_numbers():
    text = "GOOD=1\nnot a line\nlowercase=2\nEMPTY=\n"
    pairs, skipped = parse_dotenv(text)
    assert pairs == {"GOOD": "1"}
    assert skipped == [
        "line 2: no '=' found",
        'line 3: "lowercase" is not an uppercase env-var style name',
        "line 4: EMPTY has an empty value",
    ]


def test_render_plain_and_quoted():
    text = render_dotenv(
        {"B_URL": "postgres://u:p@h/db", "A_MSG": "hello world", "C": 'say "hi"'}
    )
    assert text == 'A_MSG="hello world"\nB_URL=postgres://u:p@h/db\nC="say \\"hi\\""\n'


def test_render_empty():
    assert render_dotenv({}) == ""


def test_roundtrip():
    original = {"DATABASE_URL": "postgres://u:p@h/db?ssl=true", "MSG": "a b c"}
    parsed, skipped = parse_dotenv(render_dotenv(original))
    assert parsed == original
    assert skipped == []
