"""Protocol v1 encoding, framing and command parsing (docs/protocol.md)."""
import json

import pytest

from bridge import protocol as p


def test_small_message_goes_as_bare_json():
    writes = p.frame(p.prompt_done_msg("p1"), msg_id=3)
    assert writes == [b'{"t":"prompt_done","id":"p1"}']


def test_large_message_is_framed_and_reassembles():
    msg = p.Prompt("p17", "a1b2", "perm", "Bash", "git push origin main " * 20,
                   ["Erlauben", "Immer erlauben", "Ablehnen"], danger=True).msg()
    writes = p.frame(msg, msg_id=300, chunk=64)
    assert len(writes) > 1
    assert all(len(w) <= 64 for w in writes)
    assert all(w[0] == p.FRAME_MARK and w[1] == 300 & 0xFF and w[3] == len(writes) for w in writes)
    r = p.Reassembler()
    out = [r.feed(w) for w in writes]
    assert out[:-1] == [None] * (len(writes) - 1)
    assert out[-1] == msg


def test_new_msg_id_drops_incomplete_message():
    a = p.frame(p.stt_msg("v1", "x" * 200, True), 1, chunk=64)
    b = p.frame(p.stt_msg("v2", "y" * 200, True), 2, chunk=64)
    r = p.Reassembler()
    r.feed(a[0])
    results = [r.feed(w) for w in b]
    assert results[-1]["id"] == "v2"


def test_oversized_message_rejected():
    with pytest.raises(ValueError):
        p.frame({"t": "x", "d": "z" * 5000}, 0)


def test_prompt_is_clipped_to_limits():
    m = p.Prompt("p1", "s", "ask", "T" * 80, "word " * 200,
                 [f"opt {i} " + "x" * 60 for i in range(12)]).msg()
    assert len(m["o"]) == p.MAX_OPTIONS
    assert all(len(o) <= p.MAX_OPTION_LEN for o in m["o"])
    assert len(m["tx"]) <= p.MAX_PROMPT_TEXT
    assert len(m["ti"]) <= 40


def test_prompt_validation():
    with pytest.raises(ValueError):
        p.Prompt("p", "s", "nope", "t", "x", ["a"]).msg()
    with pytest.raises(ValueError):
        p.Prompt("p", "s", "perm", "t", "x", []).msg()


def test_sessions_needing_you_come_first():
    ss = [p.Session("a", "idle one"), p.Session("b", "asks", state=p.ASKING),
          p.Session("c", "unread", unread=True)]
    m = p.sessions_msg(ss, "a")
    assert [r[0] for r in m["ss"]] == ["b", "c", "a"]
    assert m["act"] == "a"
    assert m["ss"][0] == ["b", "asks", p.ASKING, -1, "", "", "default", 0, 0]


@pytest.mark.parametrize("raw,expected", [
    ({"t": "answer", "id": "p1", "c": [2, 0, 2]}, ("answer", {"id": "p1", "choices": [0, 2]})),
    ({"t": "switch", "sid": "c3"}, ("switch", {"sid": "c3"})),
    ({"t": "set", "sid": "a", "k": "model", "v": "sonnet"},
     ("set", {"sid": "a", "key": "model", "value": "sonnet"})),
    ({"t": "set", "sid": "a", "k": "fast", "v": 1}, ("set", {"sid": "a", "key": "fast", "value": True})),
    ({"t": "ptt", "on": 1}, ("ptt", {"on": True})),
    ({"t": "stt_send", "id": "v3"}, ("stt_send", {"id": "v3"})),
])
def test_valid_commands(raw, expected):
    cmd = p.parse_command(raw)
    assert (cmd.t, cmd.args) == expected


@pytest.mark.parametrize("raw", [
    None, {"ack": True}, {"err": True}, {"t": "answer", "id": "p1", "c": []},
    {"t": "answer", "id": "p1", "c": [9]}, {"t": "set", "sid": "a", "k": "rm", "v": "x"},
    {"t": "set", "sid": "a", "k": "model", "v": ""}, {"t": "switch"}, {"t": "bogus"},
])
def test_invalid_commands_ignored(raw):
    assert p.parse_command(raw) is None


def test_reassembler_passes_bare_json_and_rejects_garbage():
    r = p.Reassembler()
    assert r.feed(b'{"ack":true}') == {"ack": True}
    assert r.feed(b"not json") is None
    assert r.feed(json.dumps([1, 2]).encode()) is None
