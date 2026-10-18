import pytest

from memcard import errors, providers
from memcard.errors import map_rclone_error

NC = providers.PROVIDERS["nextcloud"]
online = lambda: True  # noqa: E731
offline = lambda: False  # noqa: E731


def test_401_names_the_app_password_and_where_to_make_one():
    err = map_rclone_error(1, "ERROR : : 401 Unauthorized", NC, online=online)
    assert isinstance(err, errors.AuthError)
    assert "app password" in err.message and "Devices & sessions" in err.message
    assert "401" in err.detail  # raw text kept for the log, not the headline


def test_404_shows_the_expected_url_format():
    err = map_rclone_error(1, "couldn't list files: 404 Not Found", NC, online=online)
    assert isinstance(err, errors.RemoteNotFound)
    assert "file endpoint" in err.message
    assert "/remote.php/dav/files/USERNAME/" in err.hint


def test_failed_to_create_file_system_is_explained():
    err = map_rclone_error(1, "Failed to create file system for \"nc:\": didn't find section in config file", NC, online=online)
    assert "settings" in err.message and "remote.php" in err.hint


@pytest.mark.parametrize("stderr", ["dial tcp: lookup cloud.example.com: no such host", "dial tcp 1.2.3.4:443: i/o timeout"])
def test_timeouts_distinguish_offline_from_unreachable(stderr):
    assert isinstance(map_rclone_error(1, stderr, NC, online=offline), errors.Offline)
    assert isinstance(map_rclone_error(1, stderr, NC, online=online), errors.Unreachable)


def test_unknown_failure_still_gets_a_human_message():
    err = map_rclone_error(2, "something odd happened", None, online=online)
    assert err.message and "something odd" not in err.message and "something odd" in err.detail


def test_every_error_reaches_the_cli_as_an_exit_code():
    assert errors.ConflictsPending("x").exit_code == 2
    assert errors.Offline("x").exit_code == 3
    assert errors.ConfigError("x").exit_code == 4


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://cloud.example.com/remote.php/dav/files/alice/", True),
        ("https://cloud.example.com/remote.php/dav/files/alice", True),
        ("https://cloud.example.com", False),
        ("https://cloud.example.com/index.php/apps/files/", False),
        ("cloud.example.com/remote.php/dav/files/alice/", False),
        ("https://cloud.example.com/remote.php/dav/files/bob/", False),
    ],
)
def test_nextcloud_url_check_runs_before_rclone(url, ok):
    problem = NC.validate("url", url, {"user": "alice"})
    assert (problem is None) is ok
    if url == "https://cloud.example.com":
        assert "https://cloud.example.com/remote.php/dav/files/alice/" in problem


def test_detect_provider_from_existing_remote():
    assert providers.detect({"type": "webdav", "vendor": "nextcloud"}).key == "nextcloud"
    assert providers.detect({"type": "webdav", "vendor": "owncloud"}).key == "webdav"
    assert providers.detect({"type": "drive"}).oauth
    assert providers.detect({"type": "iclouddrive"}).experimental
    assert providers.detect({"type": "s3"}).label == "s3"
