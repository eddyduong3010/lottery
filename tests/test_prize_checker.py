from dataclasses import replace
from datetime import date

import pytest
from helpers import make_draw

from xsmn.prize_checker import check_ticket, validate_ticket_number


def test_ticket_validation_preserves_leading_zero() -> None:
    assert validate_ticket_number('012345') == '012345'
    with pytest.raises(ValueError):
        validate_ticket_number('12345')


def test_exact_special_prize() -> None:
    checked = check_ticket('012345', make_draw())
    assert [hit.code for hit in checked.hits] == ['db']
    assert checked.total_payout_vnd == 2_000_000_000


def test_auxiliary_special_prize() -> None:
    checked = check_ticket('912345', make_draw())
    assert [hit.code for hit in checked.hits] == ['phu_db']
    assert checked.total_payout_vnd == 50_000_000


def test_consolation_prize_requires_same_first_digit_and_one_other_mismatch() -> None:
    checked = check_ticket('012346', make_draw())
    assert [hit.code for hit in checked.hits] == ['khuyen_khich']
    assert check_ticket('112346', make_draw()).hits == ()


def test_ticket_can_win_multiple_suffix_prizes() -> None:
    draw = make_draw({'g8': ['05'], 'g5': ['3405']})
    checked = check_ticket('123405', draw)
    assert {hit.code for hit in checked.hits} == {'g8', 'g5'}
    assert checked.total_payout_vnd == 1_100_000


def test_pre_2017_ticket_is_not_valued_with_current_prize_table() -> None:
    historical_draw = replace(make_draw(), draw_date=date(2016, 12, 31))
    with pytest.raises(ValueError, match='01-01-2017'):
        check_ticket('012345', historical_draw)
