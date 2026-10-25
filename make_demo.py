"""Builds a demo Styli library: 100 albums, an Ortofon 2M Blue on the turntable and
about 80 album listens over the last three months. Side lengths are those of the
original vinyl releases, near enough for a demo.

Usage:  python3 make_demo.py [output.db]   (keep it in the folder with styli.py and styli_app)

The listens always end on the day it's run, so running it again later gives a fresh-looking demo.
It's random but seeded, so the same day gives the same library.

Steps: the album list, the library, the cartridge, listening sessions, then the listens themselves."""
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # keep this next to styli.py and styli_app
from styli_app import cartridges, common, db, library, plays  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "styli-demo.db")

# ---- the albums -----------------------------------------------------------------------------------

ALBUMS = [  # artist, title, year, side lengths
    ("Fleetwood Mac", "Rumours", 1977, "19:22 20:21"),
    ("Fleetwood Mac", "Fleetwood Mac", 1975, "21:17 21:00"),
    ("Miles Davis", "Kind of Blue", 1959, "24:45 20:59"),
    ("Miles Davis", "Bitches Brew", 1970, "27:00 26:58 25:42 14:21"),
    ("John Coltrane", "A Love Supreme", 1965, "15:07 17:47"),
    ("John Coltrane", "Blue Train", 1958, "19:58 22:27"),
    ("The Dave Brubeck Quartet", "Time Out", 1959, "19:34 19:07"),
    ("Charles Mingus", "Mingus Ah Um", 1959, "22:52 22:41"),
    ("Herbie Hancock", "Head Hunters", 1973, "15:41 25:53"),
    ("Nina Simone", "Pastel Blues", 1965, "16:45 18:55"),
    ("Pink Floyd", "The Dark Side of the Moon", 1973, "19:29 23:30"),
    ("Pink Floyd", "Wish You Were Here", 1975, "21:57 22:04"),
    ("Pink Floyd", "The Wall", 1979, "20:47 19:05 18:56 22:21"),
    ("The Beatles", "Abbey Road", 1969, "23:18 23:59"),
    ("The Beatles", "Revolver", 1966, "17:19 17:41"),
    ("The Beatles", "Sgt. Pepper's Lonely Hearts Club Band", 1967, "20:07 19:45"),
    ("The Beatles", "The Beatles", 1968, "23:35 23:16 23:15 23:27"),
    ("The Rolling Stones", "Some Girls", 1978, "20:19 20:26"),
    ("The Rolling Stones", "Sticky Fingers", 1971, "20:56 25:28"),
    ("The Rolling Stones", "Exile on Main St.", 1972, "16:42 16:32 16:56 16:57"),
    ("Led Zeppelin", "Led Zeppelin IV", 1971, "19:28 23:06"),
    ("Led Zeppelin", "Physical Graffiti", 1975, "20:06 19:58 20:34 21:37"),
    ("David Bowie", "The Rise and Fall of Ziggy Stardust and the Spiders from Mars", 1972, "19:32 18:57"),
    ("David Bowie", "Hunky Dory", 1971, "21:58 19:52"),
    ("David Bowie", "Low", 1977, "17:13 21:13"),
    ("David Bowie", "Station to Station", 1976, "20:19 17:37"),
    ("Kate Bush", "Hounds of Love", 1985, "26:14 21:25"),
    ("Kate Bush", "The Kick Inside", 1978, "21:09 22:41"),
    ("Talking Heads", "Remain in Light", 1980, "20:22 19:47"),
    ("Talking Heads", "Fear of Music", 1979, "20:39 20:16"),
    ("Joni Mitchell", "Blue", 1971, "17:49 17:52"),
    ("Joni Mitchell", "Court and Spark", 1974, "19:23 17:12"),
    ("Marvin Gaye", "What's Going On", 1971, "17:38 18:00"),
    ("Stevie Wonder", "Innervisions", 1973, "22:10 22:04"),
    ("Stevie Wonder", "Songs in the Key of Life", 1976, "21:37 22:13 23:42 20:39"),
    ("Prince and the Revolution", "Purple Rain", 1984, "20:43 23:12"),
    ("Prince", "Sign o' the Times", 1987, "20:01 19:58 20:25 19:40"),
    ("Michael Jackson", "Thriller", 1982, "19:56 22:23"),
    ("Michael Jackson", "Off the Wall", 1979, "21:58 20:30"),
    ("Bob Dylan", "Blood on the Tracks", 1975, "25:26 26:16"),
    ("Bob Dylan", "Highway 61 Revisited", 1965, "24:46 26:40"),
    ("Neil Young", "Harvest", 1972, "21:28 16:19"),
    ("Neil Young", "After the Gold Rush", 1970, "18:37 16:47"),
    ("Radiohead", "OK Computer", 1997, "14:12 13:27 12:40 13:02"),
    ("Radiohead", "In Rainbows", 2007, "21:46 20:53"),
    ("Radiohead", "Kid A", 2000, "11:55 12:19 12:16 13:26"),
    ("Nirvana", "Nevermind", 1991, "22:16 20:23"),
    ("The Smiths", "The Queen Is Dead", 1986, "19:51 17:24"),
    ("Joy Division", "Unknown Pleasures", 1979, "19:34 19:50"),
    ("New Order", "Power, Corruption & Lies", 1983, "21:41 21:14"),
    ("The Cure", "Disintegration", 1989, "17:31 18:20 17:15 18:48"),
    ("Massive Attack", "Mezzanine", 1998, "16:08 15:53 16:22 15:07"),
    ("Portishead", "Dummy", 1994, "24:08 25:12"),
    ("Daft Punk", "Random Access Memories", 2013, "18:10 18:55 19:20 17:59"),
    ("Daft Punk", "Discovery", 2001, "15:35 14:33 15:20 15:27"),
    ("Arctic Monkeys", "AM", 2013, "21:04 20:39"),
    ("Arctic Monkeys", "Whatever People Say I Am, That's What I'm Not", 2006, "20:55 20:18"),
    ("Amy Winehouse", "Back to Black", 2006, "17:47 17:09"),
    ("Adele", "21", 2011, "23:44 24:20"),
    ("Taylor Swift", "Folklore", 2020, "15:30 16:40 15:25 15:54"),
    ("Kendrick Lamar", "To Pimp a Butterfly", 2015, "19:15 20:41 19:30 19:25"),
    ("Frank Ocean", "Blonde", 2016, "15:35 15:02 14:46 14:45"),
    ("Lauryn Hill", "The Miseducation of Lauryn Hill", 1998, "19:44 18:48 19:17 19:50"),
    ("A Tribe Called Quest", "The Low End Theory", 1991, "23:58 24:05"),
    ("Beastie Boys", "Paul's Boutique", 1989, "26:52 26:20"),
    ("Public Enemy", "It Takes a Nation of Millions to Hold Us Back", 1988, "28:13 29:37"),
    ("Kraftwerk", "Trans-Europe Express", 1977, "22:42 20:10"),
    ("Kraftwerk", "The Man-Machine", 1978, "17:47 18:28"),
    ("Brian Eno", "Ambient 1: Music for Airports", 1978, "26:13 22:01"),
    ("Steely Dan", "Aja", 1977, "19:12 20:46"),
    ("Steely Dan", "Can't Buy a Thrill", 1972, "20:13 20:51"),
    ("Carole King", "Tapestry", 1971, "22:10 22:27"),
    ("Simon & Garfunkel", "Bridge over Troubled Water", 1970, "17:54 19:13"),
    ("Van Morrison", "Astral Weeks", 1968, "21:07 25:58"),
    ("Van Morrison", "Moondance", 1970, "19:47 18:40"),
    ("The Velvet Underground & Nico", "The Velvet Underground & Nico", 1967, "24:10 24:41"),
    ("Lou Reed", "Transformer", 1972, "18:59 17:48"),
    ("The Clash", "London Calling", 1979, "16:27 15:58 16:28 16:14"),
    ("Sex Pistols", "Never Mind the Bollocks, Here's the Sex Pistols", 1977, "18:16 20:27"),
    ("Patti Smith", "Horses", 1975, "18:21 24:49"),
    ("Television", "Marquee Moon", 1977, "23:12 22:48"),
    ("Blondie", "Parallel Lines", 1978, "19:27 19:31"),
    ("Queen", "A Night at the Opera", 1975, "21:17 21:51"),
    ("Elton John", "Goodbye Yellow Brick Road", 1973, "21:33 18:22 17:42 18:43"),
    ("Supertramp", "Breakfast in America", 1979, "20:47 25:19"),
    ("The Who", "Who's Next", 1971, "21:26 22:12"),
    ("The Beach Boys", "Pet Sounds", 1966, "18:50 17:07"),
    ("The Doors", "The Doors", 1967, "20:28 23:42"),
    ("The Jimi Hendrix Experience", "Electric Ladyland", 1968, "17:54 18:10 21:21 18:22"),
    ("Black Sabbath", "Paranoid", 1970, "21:00 20:59"),
    ("AC/DC", "Back in Black", 1980, "21:11 20:58"),
    ("Bruce Springsteen", "Born to Run", 1975, "19:37 19:49"),
    ("Dire Straits", "Brothers in Arms", 1985, "24:44 22:32"),
    ("Paul Simon", "Graceland", 1986, "21:10 22:08"),
    ("Tears for Fears", "Songs from the Big Chair", 1985, "19:51 21:44"),
    ("The Stone Roses", "The Stone Roses", 1989, "23:43 25:39"),
    ("Oasis", "(What's the Story) Morning Glory?", 1995, "12:20 12:39 11:49 13:19"),
    ("Pulp", "Different Class", 1995, "25:52 26:09"),
    ("Bob Marley & The Wailers", "Exodus", 1977, "18:35 18:48"),
    ("Fela Kuti & Africa 70", "Zombie", 1976, "12:26 12:19"),
]
assert len(ALBUMS) == 100, len(ALBUMS)
assert len({(a, t) for a, t, _, _ in ALBUMS}) == 100

# albums that get played a lot, and ones that come out now and then
FAVOURITES = ["Rumours", "Kind of Blue", "Hounds of Love", "Remain in Light", "In Rainbows", "Blue",
              "Random Access Memories", "Aja", "What's Going On", "Dummy"]

# ---- a fresh library ------------------------------------------------------------------------------
random.seed(2026)
if OUT.exists():
    OUT.unlink()
con = db.connect(OUT)
now = common.now_local().replace(second=0)
start = (now - dt.timedelta(days=91)).replace(hour=0, minute=0)

# ---- the library, added over the last few years -------------------------------------------------
ids = {}
for artist, title, year, lengths in ALBUMS:
    secs = [common.parse_duration(x) for x in lengths.split()]
    sides = [{"label": "ABCD"[i], "seconds": s} for i, s in enumerate(secs)]
    fmt = '2×12" Vinyl' if len(secs) == 4 else '12" Vinyl'
    rid = library.save_record(con, None, artist, title, str(year), fmt, sides)
    added = now - dt.timedelta(days=random.randint(120, 1500), minutes=random.randint(0, 1439))
    con.execute("UPDATE records SET added_at = ? WHERE id = ?", (added.isoformat(), rid))
    ids[title] = rid
con.commit()

# ---- the cartridge: fitted a week before the first listen, with Ortofon's published specifications -
fitted = (start - dt.timedelta(days=7)).replace(hour=14, minute=20)
sid = cartridges.stylus_add(con, "Ortofon 2M Blue", 1000, 0, 80, installed=fitted)
cartridges.set_stylus_details(con, sid, specs={
    "type": "Moving magnet",
    "stylus": "Nude elliptical diamond, 8/18 µm",
    "output": "5.5 mV (1 kHz, 5 cm/s)",
    "tracking": "1.8 g (1.6 to 2.0 g)",
    "compliance": "20 µm/mN",
    "frequency": "20 Hz to 20 kHz, +2/-1 dB",
    "separation": "25 dB (1 kHz)",
    "load": "47 kΩ, 150 to 300 pF",
    "weight": "7.2 g",
}, url="https://ortofon.com/products/2m-blue")
stylus = con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()

# ---- listening sessions -----------------------------------------------------------------------------
# Weekday evenings, longer weekend sessions (often two or three albums), and a holiday
# about five weeks in with nothing played, so the Stats tab has a quiet spell to show.
holiday = (start.date() + dt.timedelta(days=35), start.date() + dt.timedelta(days=44))
pool = [t for _, t, _, _ in ALBUMS]
weights = [9 if t in FAVOURITES else (2 if random.random() < 0.35 else 0.6) for t in pool]
sessions = []
day = start.date()
while day < now.date() or day == now.date():
    weekend = day.weekday() >= 5
    if not holiday[0] <= day <= holiday[1]:
        chance = 0.9 if weekend else (0.75 if day.weekday() == 4 else 0.6)
        if random.random() < chance:
            if weekend and random.random() < 0.5:
                hour, count = random.choice([11, 13, 14, 15]), random.choice([2, 3, 3])
            else:
                hour, count = random.choice([19, 20, 20, 21, 21, 22]), random.choice([1, 1, 1, 2])
            sessions.append((dt.datetime.combine(day, dt.time(hour, random.randint(0, 50))).astimezone(), count))
    day += dt.timedelta(days=1)

# ---- the listens --------------------------------------------------------------------------------------
# Each session's albums follow one another; about 80 are kept. Favourites come round more often,
# and a few listens are one side only, half of a double album, or stopped early.
listens = []
for begin, count in sessions:
    t = begin
    for _ in range(count):
        listens.append(t)
        t += dt.timedelta(minutes=random.randint(45, 60))
listens = [t for t in listens if t <= now - dt.timedelta(minutes=30)]
random.shuffle(listens)
listens = sorted(listens[:80])  # about 80 listens

last_title = None
for when in listens:
    while True:
        title = random.choices(pool, weights)[0]
        if title != last_title:
            break
    last_title = title
    rec = con.execute("SELECT * FROM records WHERE id = ?", (ids[title],)).fetchone()
    sides = {s["label"]: s for s in library.record_sides(con, rec["id"])}
    labels = sorted(sides)
    r = random.random()
    custom = None
    if r < 0.08:  # just the one side
        chosen = [random.choice(labels)]
    elif r < 0.11:  # lifted the needle early
        chosen = [labels[0]]
        custom = int(sides[labels[0]]["seconds"] * random.uniform(0.4, 0.8))
    elif len(labels) == 4 and r < 0.35:  # half of a double album
        chosen = labels[:2] if random.random() < 0.6 else labels[2:]
    else:
        chosen = labels
    played = sum(sides[c]["seconds"] for c in chosen) if custom is None else custom
    logged = (when + dt.timedelta(seconds=played)).replace(second=0, microsecond=0)
    plays.log_play(con, rec, plays.play_entries(sides, chosen, custom), logged, 1, stylus)

# ---- finish: the 2M Blue on the turntable, and a compact file ------------------------------------------
db.set_setting(con, "current_stylus", str(sid))
con.commit()
con.execute("VACUUM")
con.close()
print("wrote", OUT)
