"""
Generates quirky, human-friendly usernames for the "Continue as Guest"
login path -- color + anime character + a 3-digit number, e.g.
"CrimsonNaruto482". Pure and dependency-free (no DB access): uniqueness
against existing players is the caller's job (see web_server.py's
/api/auth/guest/suggest, which retries this against
game_history.username_is_taken).
"""
import random

_COLORS = [
    "Crimson", "Azure", "Coral", "Jade", "Scarlet", "Cobalt", "Emerald",
    "Ruby", "Sapphire", "Golden", "Obsidian", "Violet", "Teal", "Indigo",
    "Copper", "Bronze", "Charcoal", "Lavender", "Turquoise", "Maroon",
    "Amber", "Ivory", "Ebony", "Silver", "Peach", "Mint", "Slate", "Rose",
    "Onyx", "Magenta",
]

_ANIME_NAMES = [
    "Naruto", "Sasuke", "Goku", "Vegeta", "Luffy", "Zoro", "Sanji", "Nami",
    "Ichigo", "Rukia", "Levi", "Eren", "Mikasa", "Light", "Edward",
    "Alphonse", "Saitama", "Tanjiro", "Nezuko", "Zenitsu", "Inosuke",
    "Gon", "Killua", "Kirito", "Asuna", "Spike", "Usagi", "Deku",
    "Bakugo", "Todoroki", "Yusuke", "Kenshin", "Natsu", "Lucy", "Erza",
    "Gray", "Chopper", "Robin", "Franky", "Jotaro", "Dio", "Josuke",
    "Meliodas", "Guts", "Griffith", "Rem", "Emilia", "Subaru", "Mob",
    "Reigen", "Yuno", "Rei", "Asuka", "Shinji",
]


def generate_guest_username() -> str:
    color = random.choice(_COLORS)
    name = random.choice(_ANIME_NAMES)
    number = random.randint(100, 999)
    return f"{color}{name}{number}"
