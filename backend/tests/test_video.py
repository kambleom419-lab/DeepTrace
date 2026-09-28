"""The source-video route: what the Results page needs in order to play a clip back.

The evidence route can answer a bare `image/jpeg`, but a video is held to a stricter contract
by the browser: it issues range requests, refuses to seek without them, and Safari will not
play at all. These tests pin that behaviour down, plus the two things that must never regress -
the bytes come back byte-identical, and one user cannot fetch another user's footage.
"""
from __future__ import annotations


def _upload(client, auth, sample_video) -> str:
    return client.post("/api/investigations", files=sample_video, headers=auth).json()["id"]


def test_the_uploaded_bytes_come_back_unchanged(client, auth, sample_video):
    whole = sample_video["file"][1]
    inv_id = _upload(client, auth, sample_video)

    res = client.get(f"/api/investigations/{inv_id}/video", headers=auth)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("video/mp4")
    assert res.headers["accept-ranges"] == "bytes"
    assert int(res.headers["content-length"]) == len(whole)
    assert res.content == whole


def test_a_bounded_range_returns_exactly_that_slice(client, auth, sample_video):
    whole = sample_video["file"][1]
    inv_id = _upload(client, auth, sample_video)

    res = client.get(
        f"/api/investigations/{inv_id}/video",
        headers={**auth, "Range": "bytes=10-19"},
    )

    assert res.status_code == 206
    assert res.content == whole[10:20]
    assert res.headers["content-range"] == f"bytes 10-19/{len(whole)}"
    assert int(res.headers["content-length"]) == 10


def test_an_open_ended_range_runs_to_the_end(client, auth, sample_video):
    whole = sample_video["file"][1]
    inv_id = _upload(client, auth, sample_video)

    res = client.get(
        f"/api/investigations/{inv_id}/video",
        headers={**auth, "Range": "bytes=100-"},
    )

    assert res.status_code == 206
    assert res.content == whole[100:]
    assert res.headers["content-range"] == f"bytes 100-{len(whole) - 1}/{len(whole)}"


def test_a_suffix_range_counts_back_from_the_end(client, auth, sample_video):
    """`bytes=-16` means the LAST 16 bytes. Reading it as "from offset 16" is the classic bug."""
    whole = sample_video["file"][1]
    inv_id = _upload(client, auth, sample_video)

    res = client.get(
        f"/api/investigations/{inv_id}/video",
        headers={**auth, "Range": "bytes=-16"},
    )

    assert res.status_code == 206
    assert res.content == whole[-16:]


def test_a_range_past_the_end_is_416(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    res = client.get(
        f"/api/investigations/{inv_id}/video",
        headers={**auth, "Range": "bytes=999999999-"},
    )

    assert res.status_code == 416
    assert res.headers["content-range"].startswith("bytes */")


def test_a_nonsense_range_falls_back_rather_than_failing_the_request(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    res = client.get(
        f"/api/investigations/{inv_id}/video",
        headers={**auth, "Range": "bytes=abc-def"},
    )

    assert res.status_code == 416


def test_the_video_needs_a_token(client, auth, sample_video):
    """The route is not public just because a <video> tag cannot send a header."""
    inv_id = _upload(client, auth, sample_video)

    assert client.get(f"/api/investigations/{inv_id}/video").status_code == 401


def test_another_users_video_is_not_found(client, auth, sample_video):
    inv_id = _upload(client, auth, sample_video)

    client.post("/api/auth/register",
                json={"email": "snooper@example.com", "password": "pw12345"})
    token = client.post("/api/auth/login",
                        json={"email": "snooper@example.com", "password": "pw12345"}).json()
    as_snooper = {"Authorization": f"Bearer {token['access_token']}"}

    assert client.get(f"/api/investigations/{inv_id}/video",
                      headers=as_snooper).status_code == 404
