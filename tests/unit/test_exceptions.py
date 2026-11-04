import pytest

from python3_commons.exceptions import AppError


def test_app_error_is_a_plain_exception_subclass() -> None:
    assert AppError.__mro__ == (AppError, Exception, BaseException, object)


def test_app_error_carries_its_message_and_is_catchable_as_exception() -> None:
    msg = 'boom'

    with pytest.raises(Exception, match='boom') as excinfo:
        raise AppError(msg)

    assert type(excinfo.value) is AppError
    assert excinfo.value.args == ('boom',)
