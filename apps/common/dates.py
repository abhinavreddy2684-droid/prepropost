from datetime import date


def years_before(anchor: date, years: int) -> date:
    """`anchor` minus whole calendar years (29 Feb falls back to 28 Feb)."""
    try:
        return anchor.replace(year=anchor.year - years)
    except ValueError:
        return anchor.replace(year=anchor.year - years, day=28)


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
