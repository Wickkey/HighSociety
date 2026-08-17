import re

from highsociety.code.common.guest_username import generate_guest_username


def test_generate_guest_username_matches_the_color_name_number_shape():
    username = generate_guest_username()
    assert re.fullmatch(r"[A-Za-z]+[A-Za-z]+[0-9]{3}", username)


def test_generate_guest_username_ends_in_a_three_digit_number():
    for _ in range(20):
        username = generate_guest_username()
        assert username[-3:].isdigit()


def test_generate_guest_username_produces_varied_output():
    usernames = {generate_guest_username() for _ in range(50)}
    # Not a strict uniqueness guarantee (it's random), but 50 draws from a
    # large combination space collapsing to just 1-2 distinct values would
    # mean the generator is broken (e.g. always picking the same choices).
    assert len(usernames) > 10
