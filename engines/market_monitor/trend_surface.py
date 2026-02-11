# engines/market_monitor/trend_surface.py

def compute_market_trend(book: dict) -> dict:
    """
    Returns:
        { selectionId: trend_dict }
    """

    trends = {}

    for r in book.get("runners") or []:

        sid = str(r.get("selectionId"))
        ltp = r.get("lastPriceTraded")
        traded = r.get("ex", {}).get("tradedVolume") or []

        if not ltp or not traded:
            continue

        from_price = float(traded[0]["price"])
        to_price = float(ltp)

        if to_price < from_price:
            direction = "BACK->LAY"
        elif to_price > from_price:
            direction = "LAY->BACK"
        else:
            direction = "FLAT"

        ticks_moved = abs(to_price - from_price)

        volume = sum(x["size"] for x in traded)

        trends[sid] = {
            "direction": direction,
            "ticks_moved": ticks_moved,
            "from_price": from_price,
            "to_price": to_price,
            "volume": volume,
        }

    return trends
