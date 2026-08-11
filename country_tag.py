#!/usr/bin/env python3
"""country_tag.py — classify a Polymarket market into a COUNTRY from its title/tags.

Shared classifier used by country_rollup.py to break every logger's signal down
by geography (which countries carry the edge). Keyword-based, priority-ordered:
leaders/cities/teams -> country; crypto/markets/generic -> 'Global'. Stdlib only.

    from country_tag import country_of
    country_of("Will Jasmine Crockett win the Texas Democratic Senate primary?") -> "USA"
"""
import re

# country -> trigger keywords (lowercased substring match). Order matters: first hit wins.
_MAP = [
    ("USA", ["united states", " u.s.", "u.s ", "america", "trump", "biden", "harris", "vance",
             "newsom", "desantis", "fed ", "federal reserve", "fed rate", "congress", "senate",
             "house of representatives", "midterm", "nba", "nfl", "mlb", "nhl", " ncaa", "super bowl",
             "republican", "democrat", "white house", "supreme court", "texas", "california",
             "new york", "spacex", "tesla", "apple", "nasdaq", "s&p", "los angeles", "mayoral",
             "knicks", "lakers", "celtics", "spurs", "cavaliers", "pistons", "thunder", "mls cup"]),
    ("Iran", ["iran", "tehran", "khamenei", "irgc", "hormuz"]),
    ("Israel", ["israel", "netanyahu", "idf", "knesset", "gaza", "hezbollah", "hamas"]),
    ("Russia", ["russia", "putin", "kremlin", "moscow", "bank of russia", "duma"]),
    ("Ukraine", ["ukraine", "zelensky", "kyiv", "kiev"]),
    ("Venezuela", ["venezuela", "maduro", "caracas"]),
    ("Brazil", ["brazil", "lula", "bolsonaro", "brasil"]),
    ("Peru", ["peru", "fujimori", "boluarte", "lima"]),
    ("UK", ["united kingdom", " uk ", "britain", "british", "starmer", "downing street",
            "prime minister of the uk", "westminster", "england", "scotland"]),
    ("France", ["france", "french", "macron", "paris", "élysée", "elysee", "le pen"]),
    ("Germany", ["germany", "scholz", "merz", "berlin", "bundestag"]),
    ("China", ["china", "beijing", " xi ", "taiwan", "ccp", "hong kong"]),
    ("India", ["india", "modi", "new delhi", "delhi", "lok sabha"]),
    ("Argentina", ["argentina", "milei", "buenos aires"]),
    ("Mexico", ["mexico", "sheinbaum", "amlo"]),
    ("Canada", ["canada", "trudeau", "poilievre", "carney", "ottawa"]),
    ("Japan", ["japan", "tokyo", "ishiba", " ldp "]),
    ("Saudi Arabia", ["saudi", "mbs", "riyadh"]),
    ("Turkey", ["turkey", "erdogan", "ankara", "türkiye"]),
    ("Poland", ["poland", "warsaw", "tusk"]),
    ("South Korea", ["south korea", "seoul", "yoon"]),
    ("North Korea", ["north korea", "kim jong"]),
    ("Italy", ["italy", "meloni", "rome"]),
    ("Spain", ["spain", "madrid", "sanchez"]),
    ("Nigeria", ["nigeria", "tinubu", "abuja"]),
    ("Syria", ["syria", "damascus", "assad"]),
]
# World Cup / national-team sports — many "<Country> win on <date>" markets.
_NATIONS = ["netherlands", "belgium", "croatia", "portugal", "morocco", "senegal", "algeria",
            "egypt", "ghana", "cameroon", "ivory coast", "côte d'ivoire", "uruguay", "colombia",
            "ecuador", "chile", "paraguay", "australia", "iraq", "new zealand", "qatar", "denmark",
            "switzerland", "austria", "sweden", "norway", "serbia", "greece", "ireland", "wales"]
_GLOBAL_HINT = ["bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "xrp", "dogecoin",
                "up or down", "highest grossing", "eurovision", "nobel", "fifa world cup",
                "olympics", "champions league", "uefa", "f1", "formula 1", "world cup",
                "wimbledon", "golden boot", "iem ", "major 2026", "largest company", "ai model",
                "atp", "wta", "roland garros", "grand slam"]


def country_of(text):
    t = " " + (text or "").lower() + " "
    for country, kws in _MAP:
        if any(k in t for k in kws):
            return country
    for n in _NATIONS:
        if n in t:
            return n.title()
    # crypto / multinational / generic -> Global (no single country)
    if any(h in t for h in _GLOBAL_HINT):
        return "Global"
    return "Other"


if __name__ == "__main__":
    import sys
    print(country_of(" ".join(sys.argv[1:]) or "Will Jasmine Crockett win the Texas primary?"))
