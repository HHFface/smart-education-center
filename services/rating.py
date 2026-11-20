"""Elo reyting va ligalar (sof funksiyalar, bazaga bog'liq emas)."""


def expected(rating_a, rating_b):
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def k_factor(games, placement_games, k_placement, k_normal):
    """Dastlabki (joylashtirish) o'yinlarda reyting tezroq o'zgaradi."""
    return k_placement if games < placement_games else k_normal


def new_ratings(rating_a, rating_b, score_a, k_a, k_b):
    """score_a: 1 (A yutdi), 0.5 (durang), 0 (A yutqazdi). Yangi (A, B) reytinglarini qaytaradi.

    Yuqori reytingli raqibni yengish ko'proq ball beradi, pastini yengish kamroq.
    """
    exp_a = expected(rating_a, rating_b)
    exp_b = 1.0 - exp_a
    new_a = rating_a + k_a * (score_a - exp_a)
    new_b = rating_b + k_b * ((1.0 - score_a) - exp_b)
    return int(round(new_a)), int(round(new_b))


def league_for(rating, leagues):
    """leagues: [[nom, minimal_reyting], ...]"""
    result = leagues[0][0]
    for name, minimum in sorted(leagues, key=lambda item: item[1]):
        if rating >= minimum:
            result = name
    return result
