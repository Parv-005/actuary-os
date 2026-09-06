"""Number formatting — Indian (₹) conventions."""


def format_inr(value: float | int | None) -> str:
    """1200000 -> '₹12,00,000' (Indian lakh/crore grouping)."""
    if value is None:
        return "—"
    n = int(round(abs(value)))
    s = str(n)
    head, tail = s[:-3], s[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    out = ",".join(groups + [tail]) if groups else tail
    sign = "-" if value < 0 else ""
    return f"₹{sign}{out}"


def format_pct(value: float | None, decimals: int = 1) -> str:
    """0.673 -> '67.3%'."""
    if value is None:
        return "—"
    return f"{value * 100:.{decimals}f}%"


def format_pp(value: float | None, decimals: int = 1) -> str:
    """4.2 -> '+4.2pp'."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}pp"
